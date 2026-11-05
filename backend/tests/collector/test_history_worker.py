"""历史任务循环（spec 0012）：真实 PostgreSQL，假的网关会话按 before_id 翻页。"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.collector import history_worker
from harvester.collector.history_page import (
    AccountRisk,
    Fetched,
    Forbidden,
    HistoryPage,
    NotFound,
    RateLimited,
    Unavailable,
)
from harvester.collector.history_worker import HistoryDeps, Running, fetch_step
from harvester.collector.ratelimit_watch import RateLimitLog
from harvester.collector.supervisor import GatewayHandle, GatewaySession
from harvester.db import history as history_db
from harvester.db.history import CreateResult
from harvester.db.history_ranges import snowflake_floor
from harvester.db.ingest import ingest_message
from harvester.db.models import (
    AccountChannelAccess,
    ChannelCoverage,
    CollectorHeartbeat,
    HistoryJob,
    HistoryPageHour,
    HistorySettings,
    Message,
)
from harvester.db.snapshots import MessageSnapshot
from tests.db.builders import CHANNEL, make_account, message, seed_channel

Factory = async_sessionmaker[AsyncSession]
DAY = datetime(2026, 1, 1, tzinfo=UTC)


def h(hours: float) -> datetime:
    return DAY + timedelta(hours=hours)


class FakeHistory:
    """按 before_id 从新到旧切 100 条；`script` 里的结果优先按顺序返回。"""

    def __init__(
        self,
        messages: Sequence[MessageSnapshot],
        *script: HistoryPage,
        on_first_call: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.script = list(script)
        self.calls: list[int] = []
        self.on_first_call = on_first_call

    async def fetch_history_page(self, channel_id: int, before_id: int) -> HistoryPage:
        self.calls.append(before_id)
        if self.on_first_call is not None and len(self.calls) == 1:
            await self.on_first_call()
        if self.script:
            return self.script.pop(0)
        page = [m for m in self.messages if m.id < before_id][:100]
        return Fetched(
            messages=tuple(page),
            returned_ids=tuple(m.id for m in page),
            raw_count=len(page),
            request_count=1,
        )


@pytest.fixture
def factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def account_id(factory: Factory) -> int:
    async with factory() as session, session.begin():
        await seed_channel(session)
        account = await make_account(session)
        session.add(AccountChannelAccess(account_id=account, channel_id=CHANNEL.id, can_read=True))
    return account


class FakeClock:
    """可以手动拨快的时钟；`sleep` 按秒推进它，并把控制权交还给事件循环。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


def make_deps(
    factory: Factory,
    fake: FakeHistory | None,
    account_id: int,
    *,
    clock: FakeClock | None = None,
    ratelimit: RateLimitLog | None = None,
) -> HistoryDeps:
    async def no_sleep(_seconds: float) -> None:
        return None

    return HistoryDeps(
        factory=factory,
        gateway=GatewayHandle(
            session=cast(GatewaySession, fake) if fake else None, account_id=account_id
        ),
        wake=asyncio.Event(),
        clock=clock if clock is not None else (lambda: h(48)),
        uniform=(lambda _lo, hi: hi) if clock is not None else (lambda lo, _hi: lo),
        sleep=clock.sleep if clock is not None else no_sleep,
        idle_wait=0.01,
        ratelimit=ratelimit if ratelimit is not None else RateLimitLog(),
    )


async def new_job(factory: Factory, start: datetime, end: datetime) -> None:
    async with factory() as session, session.begin():
        result = await history_db.create_jobs(
            session, (CHANNEL.id,), start, end, refetch=False, origin="manual"
        )
    assert isinstance(result, CreateResult) and result.job_ids


async def run_job(deps: HistoryDeps, fake: FakeHistory, account_id: int) -> int:
    """领取一个任务并一直抓到放下它；返回抓了几次。"""
    async with deps.factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None
    current: Running | None = Running(job)
    steps = 0
    while current is not None:
        current = await fetch_step(deps, cast(GatewaySession, fake), account_id, current)
        steps += 1
        assert steps < 20
    return steps


def spread(start: datetime, count: int) -> list[MessageSnapshot]:
    """从 `start` 起每分钟一条。"""
    return [
        message(id=snowflake_floor(start + timedelta(minutes=i)), content=f"m{i}")
        for i in range(count)
    ]


async def job_row(factory: Factory) -> HistoryJob:
    async with factory() as session:
        row = await session.scalar(select(HistoryJob))
    assert row is not None
    return row


async def test_three_pages_then_done(factory: Factory, account_id: int) -> None:
    fake = FakeHistory(spread(h(1), 240))
    await new_job(factory, h(0), h(10))

    assert await run_job(make_deps(factory, fake, account_id), fake, account_id) == 3

    row = await job_row(factory)
    assert (row.status, row.fetched_count, row.pages_fetched) == ("done", 240, 3)
    assert fake.calls[0] == snowflake_floor(h(10))
    async with factory() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM message WHERE source = 'history'"))
            == 240
        )
        spans = list(await session.scalars(select(ChannelCoverage.span)))
        assert [
            (
                s.lower,
                s.upper,
            )
            for s in spans
        ] == [(h(0), h(10))]


async def test_empty_first_page_with_stored_messages_is_suspect(
    factory: Factory, account_id: int
) -> None:
    async with factory() as session, session.begin():
        await ingest_message(session, spread(h(2), 1)[0], "realtime", h(2))
    fake = FakeHistory([])
    await new_job(factory, h(0), h(1))
    await new_job(factory, h(1), h(10))
    async with factory() as session, session.begin():
        await session.execute(
            update(HistoryJob).where(HistoryJob.range_start == h(0)).values(status="paused")
        )

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    async with factory() as session:
        row = await session.scalar(select(HistoryJob).where(HistoryJob.range_start == h(1)))
        assert row is not None and (row.status, row.error) == ("failed", "suspect_empty")
        assert list(await session.scalars(select(ChannelCoverage.id))) == []
        assert await session.scalar(select(Message.deleted_at)) is None


async def test_empty_first_page_without_messages_is_done(factory: Factory, account_id: int) -> None:
    fake = FakeHistory([])
    await new_job(factory, h(0), h(1))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    assert (await job_row(factory)).status == "done"


async def test_unreadable_channel_fails_without_request(factory: Factory, account_id: int) -> None:
    fake = FakeHistory(spread(h(1), 5))
    await new_job(factory, h(0), h(10))
    async with factory() as session, session.begin():
        await session.execute(update(AccountChannelAccess).values(can_read=False))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    row = await job_row(factory)
    assert (row.status, row.error) == ("failed", "no_access")
    assert fake.calls == []


@pytest.mark.parametrize(
    ("result", "error"), [(Forbidden(), "no_access"), (NotFound(), "channel_deleted")]
)
async def test_http_failures_fail_the_job(
    factory: Factory, account_id: int, result: HistoryPage, error: str
) -> None:
    fake = FakeHistory([], result)
    await new_job(factory, h(0), h(10))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    row = await job_row(factory)
    assert (row.status, row.error) == ("failed", error)


async def test_rate_limited_and_unavailable_retry_same_page(
    factory: Factory, account_id: int
) -> None:
    fake = FakeHistory(spread(h(1), 5), RateLimited(3.0), Unavailable("http_502"))
    await new_job(factory, h(0), h(10))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    assert fake.calls == [snowflake_floor(h(10))] * 3
    row = await job_row(factory)
    assert (row.status, row.wait_reason, row.fetched_count) == ("done", None, 5)


async def test_unavailable_five_times_releases_with_not_before(
    factory: Factory, account_id: int
) -> None:
    fake = FakeHistory([], *(Unavailable("network") for _ in range(5)))
    await new_job(factory, h(0), h(10))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    row = await job_row(factory)
    assert row.status == "pending" and row.not_before is not None
    async with factory() as session, session.begin():
        assert await history_db.claim_next_job(session, account_id) is None


async def test_account_risk_pauses_everything(factory: Factory, account_id: int) -> None:
    fake = FakeHistory([], AccountRisk("captcha_required"))
    await new_job(factory, h(0), h(10))

    await run_job(make_deps(factory, fake, account_id), fake, account_id)

    assert (await job_row(factory)).status == "pending"
    async with factory() as session:
        assert await session.scalar(select(HistorySettings.paused_reason)) == "account_risk"


async def test_api_cancel_mid_job_is_not_overwritten(factory: Factory, account_id: int) -> None:
    fake = FakeHistory(spread(h(1), 250))
    await new_job(factory, h(0), h(10))
    deps = make_deps(factory, fake, account_id)
    async with factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None
    current = await fetch_step(deps, cast(GatewaySession, fake), account_id, Running(job))
    assert current is not None
    async with factory() as session, session.begin():
        await session.execute(update(HistoryJob).values(status="cancelled", finished_at=h(48)))

    assert await fetch_step(deps, cast(GatewaySession, fake), account_id, current) is None
    assert (await job_row(factory)).status == "cancelled"


async def test_loop_releases_when_offline_and_runs_when_online(
    factory: Factory, account_id: int
) -> None:
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    deps = make_deps(factory, None, account_id)
    task = asyncio.create_task(history_worker.run_history_loop(deps))
    try:
        await asyncio.sleep(0.05)
        assert (await job_row(factory)).status == "pending"
        deps.gateway.session = cast(GatewaySession, fake)
        deps.wake.set()
        for _ in range(100):
            if (await job_row(factory)).status == "done":
                break
            await asyncio.sleep(0.02)
        assert (await job_row(factory)).status == "done"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_loop_does_not_fetch_while_paused(factory: Factory, account_id: int) -> None:
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    async with factory() as session, session.begin():
        await history_db.set_history_paused(session, "manual")
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id))
    )
    try:
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert fake.calls == []
    assert (await job_row(factory)).status == "pending"


# ---- 里程碑 2：节奏、限额、暂停、抢先 ------------------------------------------------


async def until(condition: Callable[[], Awaitable[bool]], attempts: int = 250) -> None:
    for _ in range(attempts):
        if await condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("条件一直没有满足")


async def stop(task: "asyncio.Task[None]") -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def heartbeat_waiting(factory: Factory) -> tuple[str | None, datetime | None]:
    async with factory() as session:
        row = (
            await session.execute(
                select(
                    CollectorHeartbeat.history_waiting_reason,
                    CollectorHeartbeat.history_waiting_until,
                )
            )
        ).one()
    return row[0], row[1]


async def seed_heartbeat(factory: Factory) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            text("INSERT INTO collector_heartbeat (id, last_seen_at) VALUES (true, now())")
        )


async def set_settings(factory: Factory, **values: int) -> None:
    async with factory() as session, session.begin():
        await session.execute(update(HistorySettings).values(**values))


async def test_rests_after_run_minutes_then_continues(factory: Factory, account_id: int) -> None:
    """AC-5：连续抓取累计 run_minutes 后休息，任务放回 pending，休息完继续。"""
    await seed_heartbeat(factory)
    await set_settings(factory, run_minutes=5, rest_minutes=5, page_delay_min_s=1)
    await set_settings(factory, page_delay_max_s=120)
    fake = FakeHistory(spread(h(1), 2500))
    await new_job(factory, h(0), h(100))
    clock = FakeClock(h(48))
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id, clock=clock))
    )
    try:

        async def resting() -> bool:
            return (await heartbeat_waiting(factory))[0] == "rest"

        await until(resting)
        calls_at_rest = len(fake.calls)
        _, rest_until = await heartbeat_waiting(factory)
        assert rest_until is not None and rest_until == clock.now + timedelta(minutes=5)
        assert 0 < calls_at_rest < 25
        assert (await job_row(factory)).status == "pending"
        await asyncio.sleep(0.1)
        assert len(fake.calls) == calls_at_rest

        clock.now = rest_until + timedelta(seconds=1)

        async def resumed() -> bool:
            return len(fake.calls) > calls_at_rest

        await until(resumed)
        assert (await heartbeat_waiting(factory))[0] is None
    finally:
        await stop(task)


async def test_settings_change_takes_effect_on_the_next_page(
    factory: Factory, account_id: int
) -> None:
    """AC-19：改了节奏设置不用重启，下一页就用新的间隔（每轮都重读设置）。"""
    await seed_heartbeat(factory)
    await set_settings(factory, page_delay_min_s=1, page_delay_max_s=1)
    fake = FakeHistory(spread(h(1), 600))
    await new_job(factory, h(0), h(100))
    clock = FakeClock(h(48))
    delays: list[tuple[float, float]] = []

    def record(lo: float, hi: float) -> float:
        delays.append((lo, hi))
        return lo

    deps = replace(make_deps(factory, fake, account_id, clock=clock), uniform=record)
    task = asyncio.create_task(history_worker.run_history_loop(deps))
    try:

        async def used_old() -> bool:
            return (1.0, 1.0) in delays

        await until(used_old)
        await set_settings(factory, page_delay_min_s=7, page_delay_max_s=9)

        async def used_new() -> bool:
            return (7.0, 9.0) in delays

        await until(used_new)
    finally:
        await stop(task)


async def test_waits_for_quota_and_reports_resume_time(factory: Factory, account_id: int) -> None:
    """AC-5、AC-6：滚动用量到上限后不发请求，等到最早一桶滑出窗口。"""
    await seed_heartbeat(factory)
    await set_settings(factory, daily_page_limit=50)
    async with factory() as session, session.begin():
        session.add(HistoryPageHour(hour=h(47), pages=50))
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    clock = FakeClock(h(48))
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id, clock=clock))
    )
    try:

        async def waiting() -> bool:
            return (await heartbeat_waiting(factory))[0] == "quota"

        await until(waiting)
        assert (await heartbeat_waiting(factory))[1] == h(47) + timedelta(hours=25)
        assert fake.calls == [] and (await job_row(factory)).status == "pending"
    finally:
        await stop(task)


async def test_three_rate_limits_in_an_hour_pause_before_the_request(
    factory: Factory, account_id: int
) -> None:
    """AC-7：60 分钟内 3 次 429，总开关变成 rate_limited，任务回到 pending，这次请求不发。"""
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    log = RateLimitLog()
    for minutes in (50, 20, 1):
        log.record(h(48) - timedelta(minutes=minutes))
    deps = make_deps(factory, fake, account_id, ratelimit=log)
    task = asyncio.create_task(history_worker.run_history_loop(deps))
    try:

        async def paused() -> bool:
            async with factory() as session:
                return (await history_db.get_history_settings(session)).paused_reason is not None

        await until(paused)
        async with factory() as session:
            assert (await history_db.get_history_settings(session)).paused_reason == "rate_limited"
        assert fake.calls == [] and (await job_row(factory)).status == "pending"
    finally:
        await stop(task)


async def test_two_rate_limits_or_old_ones_do_not_pause(factory: Factory, account_id: int) -> None:
    """AC-7：两次不暂停；一小时以前的不算。"""
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    log = RateLimitLog()
    for minutes in (200, 90, 30, 10):
        log.record(h(48) - timedelta(minutes=minutes))
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id, ratelimit=log))
    )
    try:

        async def done() -> bool:
            return (await job_row(factory)).status == "done"

        await until(done)
    finally:
        await stop(task)
    async with factory() as session:
        assert (await history_db.get_history_settings(session)).paused_reason is None


async def test_resume_clears_the_rate_limit_count_without_notify(
    factory: Factory, account_id: int
) -> None:
    """AC-8：原因从有值变成空时清空 429 计数，不依赖 NOTIFY。"""
    fake = FakeHistory(spread(h(1), 30))
    await new_job(factory, h(0), h(10))
    log = RateLimitLog()
    for minutes in (3, 2, 1):
        log.record(h(48) - timedelta(minutes=minutes))
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id, ratelimit=log))
    )
    try:

        async def paused() -> bool:
            async with factory() as session:
                return (await history_db.get_history_settings(session)).paused_reason is not None

        await until(paused)
        async with factory() as session, session.begin():
            await session.execute(
                update(HistorySettings).values(paused_reason=None, paused_at=None)
            )

        async def done() -> bool:
            return (await job_row(factory)).status == "done"

        await until(done)  # 靠 30 毫秒的兜底轮询发现，没有发 NOTIFY
        assert len(log.hits) == 0
    finally:
        await stop(task)


async def test_yields_to_gap_auto_then_resumes_from_cursor(
    factory: Factory, account_id: int
) -> None:
    """AC-10：手动任务抓了一页后被 gap_auto 抢先，断点保留，gap_auto 先完成，然后从断点继续。"""
    fake = FakeHistory(spread(h(1), 300))

    async def add_gap_auto() -> None:
        async with factory() as session, session.begin():
            await history_db.create_jobs(
                session, (CHANNEL.id,), h(20), h(24), refetch=False, origin="gap_auto"
            )

    fake.on_first_call = add_gap_auto
    await new_job(factory, h(0), h(10))
    task = asyncio.create_task(
        history_worker.run_history_loop(make_deps(factory, fake, account_id))
    )
    try:

        async def both_done() -> bool:
            async with factory() as session:
                statuses = list(await session.scalars(select(HistoryJob.status)))
            return statuses == ["done", "done"]

        await until(both_done)
    finally:
        await stop(task)
    async with factory() as session:
        manual = await session.scalar(select(HistoryJob).where(HistoryJob.origin == "manual"))
        auto = await session.scalar(select(HistoryJob).where(HistoryJob.origin == "gap_auto"))
    assert manual is not None and auto is not None
    assert auto.finished_at is not None and manual.finished_at is not None
    assert auto.finished_at < manual.finished_at
    assert manual.fetched_count == 300
    # 手动任务：第一页、gap_auto 的一页、然后从断点（第一页最旧一条）继续。
    assert fake.calls[0] == snowflake_floor(h(10))
    assert fake.calls[1] == snowflake_floor(h(24))
    assert fake.calls[2] < fake.calls[0]


async def test_job_preempted_three_times_does_not_yield(factory: Factory, account_id: int) -> None:
    """AC-10：连续被抢先 3 次后，下一次不再让位；gap_auto 自己也不让位。"""
    await new_job(factory, h(0), h(10))
    async with factory() as session, session.begin():
        await history_db.create_jobs(
            session, (CHANNEL.id,), h(20), h(24), refetch=False, origin="gap_auto"
        )
    deps = make_deps(factory, None, account_id)
    base = Running(
        history_db.ClaimedJob(
            id=1,
            channel_id=CHANNEL.id,
            origin="manual",
            refetch=False,
            range_start=h(0),
            range_end=h(10),
            cursor_message_id=None,
            preempted_count=2,
        )
    )
    assert await history_worker._should_yield(deps, base)  # pyright: ignore[reportPrivateUsage]
    exempt = Running(replace(base.job, preempted_count=3))
    assert not await history_worker._should_yield(deps, exempt)  # pyright: ignore[reportPrivateUsage]
    auto = Running(replace(base.job, origin="gap_auto"))
    assert not await history_worker._should_yield(deps, auto)  # pyright: ignore[reportPrivateUsage]


async def test_release_preempted_counts_and_commit_resets(
    factory: Factory, account_id: int
) -> None:
    """AC-10：让位一次加 1，抓到一页后清零。"""
    fake = FakeHistory(spread(h(1), 300))
    await new_job(factory, h(0), h(10))
    async with factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None
    async with factory() as session, session.begin():
        assert await history_db.release_job(session, job.id, preempted=True)
    assert (await job_row(factory)).preempted_count == 1
    async with factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None and job.preempted_count == 1
    current = await fetch_step(
        make_deps(factory, fake, account_id), cast(GatewaySession, fake), account_id, Running(job)
    )
    assert current is not None
    assert (await job_row(factory)).preempted_count == 0


async def test_unhealthy_ratelimit_watch_pauses_at_startup(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-7：日志钩子收不到记录时，启动就把总开关关闭。"""
    import logging

    logger = logging.getLogger("discord.http")
    monkeypatch.setattr(logger, "level", logging.ERROR)
    assert await history_worker.check_ratelimit_watch(factory) is False
    async with factory() as session:
        assert (await history_db.get_history_settings(session)).paused_reason == "rate_limited"


async def test_healthy_ratelimit_watch_leaves_switch_alone(factory: Factory) -> None:
    assert await history_worker.check_ratelimit_watch(factory) is True
    async with factory() as session:
        assert (await history_db.get_history_settings(session)).paused_reason is None


async def test_heartbeat_loop_writes_history_seen_at(factory: Factory, account_id: int) -> None:
    await seed_heartbeat(factory)
    deps = make_deps(factory, None, account_id)
    task = asyncio.create_task(history_worker.run_heartbeat_loop(deps))
    try:

        async def beat() -> bool:
            async with factory() as session:
                return (
                    await session.scalar(select(CollectorHeartbeat.history_worker_seen_at))
                ) is not None

        await until(beat)
    finally:
        await stop(task)


async def test_paused_job_gets_no_further_request(factory: Factory, account_id: int) -> None:
    fake = FakeHistory(spread(h(1), 250))
    await new_job(factory, h(0), h(10))
    deps = make_deps(factory, fake, account_id)
    async with factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None
    current = await fetch_step(deps, cast(GatewaySession, fake), account_id, Running(job))
    assert current is not None and len(fake.calls) == 1
    async with factory() as session, session.begin():
        await history_db.transition_job(session, job.id, "pause")

    assert await fetch_step(deps, cast(GatewaySession, fake), account_id, current) is None
    assert len(fake.calls) == 1
    assert (await job_row(factory)).status == "paused"


async def test_cleanup_loop_deletes_old_rows_then_sleeps(factory: Factory, account_id: int) -> None:
    from harvester.collector.history_worker import CLEANUP_INTERVAL, run_cleanup_loop

    async with factory() as session, session.begin():
        session.add(HistoryPageHour(hour=h(0), pages=5))
    slept: list[float] = []

    async def stop_after_sleep(seconds: float) -> None:
        slept.append(seconds)
        raise asyncio.CancelledError

    deps = replace(make_deps(factory, None, account_id), sleep=stop_after_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_cleanup_loop(deps)

    assert slept == [CLEANUP_INTERVAL]
    async with factory() as session:
        assert await session.scalar(select(HistoryPageHour.hour)) is None
