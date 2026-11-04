"""历史任务循环（spec 0012）：真实 PostgreSQL，假的网关会话按 before_id 翻页。"""

import asyncio
from collections.abc import Sequence
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
from harvester.collector.supervisor import GatewayHandle, GatewaySession
from harvester.db import history as history_db
from harvester.db.history import CreateResult
from harvester.db.history_ranges import snowflake_floor
from harvester.db.ingest import ingest_message
from harvester.db.models import (
    AccountChannelAccess,
    ChannelCoverage,
    HistoryJob,
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

    def __init__(self, messages: Sequence[MessageSnapshot], *script: HistoryPage) -> None:
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.script = list(script)
        self.calls: list[int] = []

    async def fetch_history_page(self, channel_id: int, before_id: int) -> HistoryPage:
        self.calls.append(before_id)
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


def make_deps(factory: Factory, fake: FakeHistory | None, account_id: int) -> HistoryDeps:
    async def no_sleep(_seconds: float) -> None:
        return None

    return HistoryDeps(
        factory=factory,
        gateway=GatewayHandle(
            session=cast(GatewaySession, fake) if fake else None, account_id=account_id
        ),
        wake=asyncio.Event(),
        clock=lambda: h(48),
        uniform=lambda lo, _hi: lo,
        sleep=no_sleep,
        idle_wait=0.01,
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
