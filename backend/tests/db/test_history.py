"""spec 0012 的数据库函数：建任务的范围扣减、领取顺序、提交一页、失败和放回（真实 PostgreSQL）。"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.db import history as history_db
from harvester.db import raw
from harvester.db.history import ClaimedJob, CreateResult, InvalidRange
from harvester.db.history_ranges import snowflake_floor
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    HistoryJob,
    HistoryPageHour,
    Message,
)
from harvester.db.snapshots import MessageSnapshot, snowflake_time
from tests.db.builders import (
    CHANNEL,
    OTHER_CHANNEL,
    get_revisions,
    make_account,
    message,
    seed_channel,
    seed_other_channel,
)

Factory = async_sessionmaker[AsyncSession]
DAY = datetime(2026, 1, 1, tzinfo=UTC)


def h(hours: float) -> datetime:
    return DAY + timedelta(hours=hours)


def msg_at(t: datetime, n: int = 0) -> MessageSnapshot:
    return message(id=snowflake_floor(t) + n, content=f"m{n}")


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


async def create(
    factory: Factory,
    start: datetime | None,
    end: datetime | None,
    *,
    refetch: bool = False,
    ids: tuple[int, ...] = (CHANNEL.id,),
) -> CreateResult:
    async with factory() as session, session.begin():
        result = await history_db.create_jobs(
            session, ids, start, end, refetch=refetch, origin="manual"
        )
    assert isinstance(result, CreateResult)
    return result


async def job_ranges(factory: Factory) -> list[tuple[datetime, datetime]]:
    async with factory() as session:
        rows = await session.execute(
            select(HistoryJob.range_start, HistoryJob.range_end).order_by(HistoryJob.range_start)
        )
        return [(r.range_start, r.range_end) for r in rows]


# ---- 建任务（AC-1、AC-2）-------------------------------------------------------------


async def test_create_subtracts_coverage_and_open_jobs(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        session.add(
            ChannelCoverage(channel_id=CHANNEL.id, span=Range(h(6), h(12)), source="realtime")
        )
    await create(factory, h(18), h(20))

    result = await create(factory, h(0), h(24))

    assert len(result.job_ids) == 3
    assert await job_ranges(factory) == [
        (h(0), h(6)),
        (h(12), h(18)),
        (h(18), h(20)),
        (h(20), h(24)),
    ]
    async with factory() as session:
        batches = set(
            await session.scalars(
                select(HistoryJob.batch_id).where(HistoryJob.id.in_(result.job_ids))
            )
        )
    assert len(batches) == 1 and result.batch_id in batches


async def test_refetch_only_subtracts_open_jobs(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        session.add(
            ChannelCoverage(channel_id=CHANNEL.id, span=Range(h(6), h(12)), source="realtime")
        )
    await create(factory, h(18), h(20))

    result = await create(factory, h(0), h(24), refetch=True)

    assert len(result.job_ids) == 2
    async with factory() as session:
        rows = await session.execute(
            select(HistoryJob.range_start, HistoryJob.range_end, HistoryJob.refetch)
            .where(HistoryJob.id.in_(result.job_ids))
            .order_by(HistoryJob.range_start)
        )
        assert [tuple(r) for r in rows] == [(h(0), h(18), True), (h(20), h(24), True)]


async def test_fully_covered_is_nothing_to_fetch(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        session.add(
            ChannelCoverage(channel_id=CHANNEL.id, span=Range(h(0), None), source="realtime")
        )

    result = await create(factory, h(1), h(2))

    assert result.batch_id is None
    assert [(s.channel_id, s.reason) for s in result.skipped] == [(CHANNEL.id, "nothing_to_fetch")]


async def test_skip_reasons_and_dedupe(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        await seed_other_channel(session)  # 小号没有访问行
        # 分类频道 200 不能采集；把它设成可读，确保原因是 not_collectable。
        session.add(AccountChannelAccess(account_id=account_id, channel_id=200, can_read=True))

    result = await create(factory, h(0), h(1), ids=(CHANNEL.id, CHANNEL.id, 999, 200, 211))

    assert len(result.job_ids) == 1
    assert [(s.channel_id, s.reason) for s in result.skipped] == [
        (999, "not_found"),
        (200, "not_collectable"),
        (211, "no_access"),
    ]


async def test_deleted_channel_is_skipped(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(status="deleted")
        )

    result = await create(factory, h(0), h(1))

    assert [s.reason for s in result.skipped] == ["channel_deleted"]


async def test_full_history_starts_at_channel_creation(factory: Factory, account_id: int) -> None:
    await create(factory, None, h(1))

    assert await job_ranges(factory) == [(snowflake_time(CHANNEL.id), h(1))]


async def test_end_in_future_is_now_and_start_after_end_is_invalid(
    factory: Factory, account_id: int
) -> None:
    future = datetime.now(UTC) + timedelta(days=2)
    await create(factory, h(0), future)
    (_, end), *_ = await job_ranges(factory)
    assert end < future

    async with factory() as session, session.begin():
        assert isinstance(
            await history_db.create_jobs(
                session, (CHANNEL.id,), future, None, refetch=False, origin="manual"
            ),
            InvalidRange,
        )


async def test_concurrent_creates_do_not_overlap(factory: Factory, account_id: int) -> None:
    results = await asyncio.gather(create(factory, h(0), h(10)), create(factory, h(5), h(15)))

    assert sum(len(r.job_ids) for r in results) == 2
    ranges = await job_ranges(factory)
    assert ranges == [(h(0), h(10)), (h(10), h(15))] or ranges == [
        (h(0), h(5)),
        (h(5), h(15)),
    ]


# ---- 领取、提交一页、结束（AC-3、AC-3a）------------------------------------------------


async def claim(factory: Factory, account_id: int) -> ClaimedJob:
    async with factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    assert job is not None
    return job


async def test_claim_prefers_gap_auto_then_id(factory: Factory, account_id: int) -> None:
    await create(factory, h(0), h(1))
    async with factory() as session, session.begin():
        result = await history_db.create_jobs(
            session, (CHANNEL.id,), h(2), h(3), refetch=False, origin="gap_auto"
        )
    assert isinstance(result, CreateResult)

    job = await claim(factory, account_id)

    assert job.origin == "gap_auto"
    async with factory() as session:
        running = await session.scalar(select(HistoryJob).where(HistoryJob.id == job.id))
        assert running is not None and running.status == "running"
        assert running.account_id == account_id and running.started_at is not None


async def test_only_one_job_runs_at_a_time(factory: Factory, account_id: int) -> None:
    await create(factory, h(0), h(1))
    await create(factory, h(2), h(3))
    await claim(factory, account_id)

    with pytest.raises(Exception, match="uq_history_job_running"):
        await claim(factory, account_id)


async def commit(
    factory: Factory, job: ClaimedJob, snaps: list[MessageSnapshot], raw_count: int | None = None
) -> history_db.PageCommitted:
    ids = tuple(s.id for s in snaps)
    async with factory() as session, session.begin():
        return await history_db.commit_page(
            session, job, snaps, ids, len(snaps) if raw_count is None else raw_count, 1, h(30)
        )


async def test_commit_full_page_moves_cursor_then_short_page_finishes(
    factory: Factory, account_id: int
) -> None:
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    first = [msg_at(h(9), n) for n in range(100)]

    committed = await commit(factory, job, first)

    assert committed.status == "running"
    assert committed.cursor_message_id == snowflake_floor(h(9))
    job = ClaimedJob(**{**job.__dict__, "cursor_message_id": committed.cursor_message_id})
    # 第二页带一条早于起点的消息：丢掉它，到底了。
    second = [msg_at(h(5), 1), msg_at(h(-1), 0)]
    committed = await commit(factory, job, second)

    assert committed.status == "done"
    async with factory() as session:
        row = await session.scalar(select(HistoryJob).where(HistoryJob.id == job.id))
        assert row is not None
        assert (row.fetched_count, row.pages_fetched, row.finished_at is not None) == (101, 2, True)
        spans = list(await session.scalars(select(ChannelCoverage.span)))
        assert [(s.lower, s.upper) for s in spans] == [(h(0), h(10))]
        sources = set(await session.scalars(select(Message.source)))
        assert sources == {"history"}
        assert await session.scalar(select(HistoryPageHour.pages)) == 2
        assert (
            await session.scalar(select(Message.id).where(Message.id < snowflake_floor(h(0))))
            is None
        )


async def test_commit_on_cancelled_job_keeps_messages_but_not_row(
    factory: Factory, account_id: int
) -> None:
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    async with factory() as session, session.begin():
        await session.execute(
            update(HistoryJob).where(HistoryJob.id == job.id).values(status="cancelled")
        )

    committed = await commit(factory, job, [msg_at(h(5))])

    assert committed.status == "cancelled"
    async with factory() as session:
        row = await session.scalar(select(HistoryJob).where(HistoryJob.id == job.id))
        assert row is not None and row.cursor_message_id is None and row.pages_fetched == 0
        assert await session.scalar(select(Message.id)) == snowflake_floor(h(5))
        assert list(await session.scalars(select(ChannelCoverage.id))) == []


async def test_realtime_message_stays_single_row(factory: Factory, account_id: int) -> None:
    from harvester.db.ingest import ingest_message

    snap = msg_at(h(5))
    async with factory() as session, session.begin():
        await ingest_message(session, snap, "realtime", h(5))
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)

    await commit(factory, job, [snap])

    async with factory() as session:
        rows = list(await session.scalars(select(Message.source)))
        assert rows == ["realtime"]


async def test_backfill_with_different_content_adds_a_revision(
    factory: Factory, account_id: int
) -> None:
    """AC-4：补采的快照内容不同时按编辑判断表多记一个版本，旧内容不丢，`source` 不变。"""
    from harvester.db.ingest import ingest_message

    first = msg_at(h(5))
    async with factory() as session, session.begin():
        await ingest_message(session, first, "realtime", h(5))
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)

    await commit(factory, job, [replace(first, content="改过了", edited_at=h(6))])

    async with factory() as session:
        rows = (
            await session.execute(select(Message.source, Message.content, Message.version))
        ).all()
        assert [tuple(r) for r in rows] == [("realtime", "改过了", 2)]
        revisions = await get_revisions(session, first.id)
        assert [(r.version, r.content) for r in revisions] == [(1, "m0")]


async def test_commit_notifies_history_changed_and_never_new_message(
    factory: Factory, account_id: int
) -> None:
    """AC-3、AC-17：每页提交发一次 `history_changed`，补采的消息不发 `new_message`。"""
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    received: list[str] = []

    def on_notify(_conn: object, _pid: int, channel: str, _payload: object) -> None:
        received.append(channel)

    conn = await raw.connect()
    try:
        await conn.add_listener(raw.HISTORY_CHANGED_CHANNEL, on_notify)
        await conn.add_listener(raw.NEW_MESSAGE_CHANNEL, on_notify)

        await commit(factory, job, [msg_at(h(5))])
        await asyncio.sleep(0.1)

        assert received == [raw.HISTORY_CHANGED_CHANNEL]
    finally:
        await conn.close()


async def test_fail_writes_partial_coverage_except_suspect_empty(
    factory: Factory, account_id: int
) -> None:
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    await commit(factory, job, [msg_at(h(9), n) for n in range(100)])

    async with factory() as session, session.begin():
        assert await history_db.fail_job(session, job.id, "no_access")
        # 已经不是 running：再失败一次影响 0 行。
        assert not await history_db.fail_job(session, job.id, "no_access")

    async with factory() as session:
        spans = list(await session.scalars(select(ChannelCoverage.span)))
        assert [(s.lower, s.upper) for s in spans] == [(h(9) + timedelta(milliseconds=1), h(10))]

    await create(factory, h(20), h(21))
    other = await claim(factory, account_id)
    async with factory() as session, session.begin():
        assert await history_db.fail_job(session, other.id, "suspect_empty")
    async with factory() as session:
        assert len(list(await session.scalars(select(ChannelCoverage.id)))) == 1


async def test_has_stored_messages_ignores_deleted_and_out_of_range(
    factory: Factory, account_id: int
) -> None:
    from harvester.db.ingest import ingest_message, mark_messages_deleted

    async with factory() as session, session.begin():
        await ingest_message(session, msg_at(h(5)), "realtime", h(5))
        await ingest_message(session, msg_at(h(15)), "realtime", h(15))
        await mark_messages_deleted(session, [snowflake_floor(h(5))], h(6), "realtime")
        assert not await history_db.has_stored_messages(session, CHANNEL.id, h(0), h(10))
        assert await history_db.has_stored_messages(session, CHANNEL.id, h(0), h(20))


async def test_release_and_reset_put_running_back(factory: Factory, account_id: int) -> None:
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    async with factory() as session, session.begin():
        assert await history_db.release_job(session, job.id)
        assert not await history_db.release_job(session, job.id)
    job = await claim(factory, account_id)
    async with factory() as session, session.begin():
        assert await history_db.reset_running_on_start(session) == 1
    async with factory() as session:
        assert await session.scalar(select(HistoryJob.status)) == "pending"


async def test_channel_readable_follows_access_row(factory: Factory, account_id: int) -> None:
    async with factory() as session, session.begin():
        assert await history_db.channel_readable(session, account_id, CHANNEL.id)
        await session.execute(
            update(AccountChannelAccess)
            .where(AccountChannelAccess.channel_id == CHANNEL.id)
            .values(can_read=False)
        )
        assert not await history_db.channel_readable(session, account_id, CHANNEL.id)


async def test_list_active_with_batch_totals(factory: Factory, account_id: int) -> None:
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)
    await commit(factory, job, [msg_at(h(9), n) for n in range(100)])

    async with factory() as session:
        page = await history_db.list_jobs(session, "active")

    assert [j.status for j in page.jobs] == ["running"]
    (batch,) = page.batches
    assert (batch.channel_count, batch.fetched_count, batch.status_counts["running"]) == (1, 100, 1)


# ---- 任务操作（AC-9、AC-11）---------------------------------------------------------


async def job_status(factory: Factory, job_id: int) -> str:
    async with factory() as session:
        status = await session.scalar(select(HistoryJob.status).where(HistoryJob.id == job_id))
    assert status is not None
    return status


async def act(factory: Factory, job_id: int, action: history_db.JobAction) -> object:
    async with factory() as session, session.begin():
        return await history_db.transition_job(session, job_id, action)


async def test_every_legal_transition_works_and_illegal_ones_report_current(
    factory: Factory, account_id: int
) -> None:
    (job_id,) = (await create(factory, h(0), h(10))).job_ids

    assert await act(factory, job_id, "pause") == history_db.Transitioned(job_id)
    assert await job_status(factory, job_id) == "paused"
    assert await act(factory, job_id, "pause") == history_db.InvalidTransition("paused")
    assert await act(factory, job_id, "retry") == history_db.InvalidTransition("paused")
    assert await act(factory, job_id, "resume") == history_db.Transitioned(job_id)
    assert await job_status(factory, job_id) == "pending"
    assert await act(factory, job_id, "resume") == history_db.InvalidTransition("pending")
    running = await claim(factory, account_id)
    assert await act(factory, running.id, "pause") == history_db.Transitioned(job_id)
    assert await act(factory, job_id, "cancel") == history_db.Transitioned(job_id)
    assert await job_status(factory, job_id) == "cancelled"
    assert await act(factory, job_id, "cancel") == history_db.InvalidTransition("cancelled")
    assert await act(factory, 999_999, "pause") == history_db.JobNotFound()


async def test_retry_failed_keeps_cursor_and_clears_error(
    factory: Factory, account_id: int
) -> None:
    (job_id,) = (await create(factory, h(0), h(10))).job_ids
    job = await claim(factory, account_id)
    await commit(factory, job, [msg_at(h(9), n) for n in range(100)])
    async with factory() as session, session.begin():
        await history_db.fail_job(session, job_id, "unexpected")

    assert await act(factory, job_id, "retry") == history_db.Transitioned(job_id)

    async with factory() as session:
        row = await session.scalar(select(HistoryJob).where(HistoryJob.id == job_id))
        assert row is not None
        assert (row.status, row.error, row.finished_at) == ("pending", None, None)
        assert row.cursor_message_id == snowflake_floor(h(9))


async def test_cancel_with_cursor_writes_partial_coverage_once(
    factory: Factory, account_id: int
) -> None:
    (job_id,) = (await create(factory, h(0), h(10))).job_ids
    job = await claim(factory, account_id)
    await commit(factory, job, [msg_at(h(9), n) for n in range(100)])

    assert await act(factory, job_id, "cancel") == history_db.Transitioned(job_id)

    async with factory() as session:
        spans = list(await session.scalars(select(ChannelCoverage.span)))
        assert [(s.lower, s.upper) for s in spans] == [(h(9) + timedelta(milliseconds=1), h(10))]
        row = await session.scalar(select(HistoryJob).where(HistoryJob.id == job_id))
        assert row is not None and row.finished_at is not None


async def test_cancel_without_cursor_or_after_failure_writes_no_coverage(
    factory: Factory, account_id: int
) -> None:
    (no_cursor,) = (await create(factory, h(0), h(1))).job_ids
    assert await act(factory, no_cursor, "cancel") == history_db.Transitioned(no_cursor)
    (failed,) = (await create(factory, h(20), h(30))).job_ids
    job = await claim(factory, account_id)
    await commit(factory, job, [msg_at(h(29), n) for n in range(100)])
    async with factory() as session, session.begin():
        await history_db.fail_job(session, failed, "no_access")
    async with factory() as session:
        before = len(list(await session.scalars(select(ChannelCoverage.id))))

    assert await act(factory, failed, "cancel") == history_db.Transitioned(failed)

    async with factory() as session:
        assert len(list(await session.scalars(select(ChannelCoverage.id)))) == before == 1


async def test_pause_writes_no_coverage_and_collector_writes_after_pause_are_dropped(
    factory: Factory, account_id: int
) -> None:
    (job_id,) = (await create(factory, h(0), h(10))).job_ids
    job = await claim(factory, account_id)
    await act(factory, job_id, "pause")

    async with factory() as session:
        assert list(await session.scalars(select(ChannelCoverage.id))) == []
    async with factory() as session, session.begin():
        assert not await history_db.set_job_wait(session, job.id, "retrying", h(31))
        assert not await history_db.release_job(session, job.id)
        assert not await history_db.fail_job(session, job.id, "unexpected")
    assert await job_status(factory, job_id) == "paused"
    # 暂停时正在提交的那一页仍然写入、断点更新。
    committed = await commit(factory, job, [msg_at(h(9), n) for n in range(100)])
    assert committed.status == "paused" and committed.cursor_message_id == snowflake_floor(h(9))


async def test_batch_action_only_changes_matching_rows(factory: Factory, account_id: int) -> None:
    result = await create(factory, h(0), h(10), ids=(CHANNEL.id,))
    assert result.batch_id is not None
    async with factory() as session, session.begin():
        await seed_other_channel(session)
        session.add(
            AccountChannelAccess(account_id=account_id, channel_id=OTHER_CHANNEL.id, can_read=True)
        )
    other = await create(factory, h(0), h(10), ids=(OTHER_CHANNEL.id,))
    assert other.batch_id is not None
    async with factory() as session, session.begin():
        await session.execute(
            update(HistoryJob).where(HistoryJob.batch_id == other.batch_id).values(status="paused")
        )

    async with factory() as session, session.begin():
        changed = await history_db.transition_batch(session, result.batch_id, "pause")
    assert changed == 1
    async with factory() as session, session.begin():
        assert await history_db.transition_batch(session, result.batch_id, "pause") == 0
        assert await history_db.transition_batch(session, other.batch_id, "resume") == 1
        assert isinstance(
            await history_db.transition_batch(session, uuid.uuid4(), "cancel"),
            history_db.BatchNotFound,
        )


async def test_channel_scoped_action_leaves_the_other_channel_alone(
    factory: Factory, account_id: int
) -> None:
    """AC-9：一个批次里两个频道各两段，按频道暂停只改这个频道的段，`done` 的段不算。"""
    async with factory() as session, session.begin():
        await seed_other_channel(session)
        session.add(
            AccountChannelAccess(account_id=account_id, channel_id=OTHER_CHANNEL.id, can_read=True)
        )
        # 两个频道各留一段已覆盖的时间，建任务时各拆成两段。
        for channel_id in (CHANNEL.id, OTHER_CHANNEL.id):
            session.add(
                ChannelCoverage(channel_id=channel_id, span=Range(h(4), h(6)), source="realtime")
            )
    result = await create(factory, h(0), h(10), ids=(CHANNEL.id, OTHER_CHANNEL.id))
    assert result.batch_id is not None and len(result.job_ids) == 4

    async with factory() as session, session.begin():
        changed = await history_db.transition_batch(session, result.batch_id, "pause", CHANNEL.id)

    assert changed == 2
    async with factory() as session:
        rows = await session.execute(select(HistoryJob.channel_id, HistoryJob.status))
        by_channel: dict[int, list[str]] = {}
        for row in rows:
            by_channel.setdefault(row.channel_id, []).append(row.status)
        assert sorted(by_channel[CHANNEL.id]) == ["paused", "paused"]
        assert sorted(by_channel[OTHER_CHANNEL.id]) == ["pending", "pending"]

    # 已经 done 的段不算在内：把另一个频道的两段标成 done，按频道暂停改 0 行。
    async with factory() as session, session.begin():
        await session.execute(
            update(HistoryJob)
            .where(HistoryJob.channel_id == OTHER_CHANNEL.id)
            .values(status="done")
        )
    async with factory() as session, session.begin():
        assert (
            await history_db.transition_batch(session, result.batch_id, "pause", OTHER_CHANNEL.id)
            == 0
        )

    # 批次里没有这个频道、批次本身不存在，都是 BatchNotFound。
    async with factory() as session, session.begin():
        assert isinstance(
            await history_db.transition_batch(session, result.batch_id, "cancel", 999),
            history_db.BatchNotFound,
        )
        assert isinstance(
            await history_db.transition_batch(session, uuid.uuid4(), "cancel", CHANNEL.id),
            history_db.BatchNotFound,
        )


# ---- 整段重抓对账（AC-13）-----------------------------------------------------------


async def ingest_stored(factory: Factory, *snaps: MessageSnapshot) -> None:
    from harvester.db.ingest import ingest_message

    async with factory() as session, session.begin():
        for snap in snaps:
            await ingest_message(session, snap, "realtime", h(5))


async def deleted_ids(factory: Factory) -> dict[int, str | None]:
    async with factory() as session:
        rows = await session.execute(
            select(Message.id, Message.delete_source).where(Message.deleted_at.is_not(None))
        )
        return {r.id: r.delete_source for r in rows}


async def test_refetch_marks_missing_messages_in_the_page_interval(
    factory: Factory, account_id: int
) -> None:
    ids = [snowflake_floor(h(9)) + n for n in (10, 8, 7)]
    gone = snowflake_floor(h(9)) + 9
    outside = snowflake_floor(h(9) + timedelta(seconds=2))  # 比这一页的「之前」位置还新，不动
    below = snowflake_floor(h(9)) + 3  # 比这一页最旧的一条还旧，下一页才会对账
    await ingest_stored(
        factory, message(id=gone), message(id=outside), message(id=below), message(id=ids[2])
    )
    await create(factory, h(0), h(9) + timedelta(seconds=1), refetch=True)
    job = await claim(factory, account_id)
    before = job.range_end

    snaps = [message(id=i) for i in ids]
    async with factory() as session, session.begin():
        await history_db.commit_page(session, job, snaps, tuple(ids), 100, 1, h(30))

    assert before > h(9)
    assert await deleted_ids(factory) == {gone: "reconciled"}


async def test_without_refetch_nothing_is_marked(factory: Factory, account_id: int) -> None:
    base = snowflake_floor(h(5))
    await ingest_stored(factory, message(id=base + 5))
    await create(factory, h(0), h(10))
    job = await claim(factory, account_id)

    await commit(factory, job, [message(id=base + 9), message(id=base + 1)], raw_count=100)

    assert await deleted_ids(factory) == {}


async def test_refetch_last_and_empty_page_reconcile_down_to_range_start(
    factory: Factory, account_id: int
) -> None:
    old = snowflake_floor(h(1))
    await ingest_stored(factory, message(id=old))
    await create(factory, h(0), h(10), refetch=True)
    job = await claim(factory, account_id)

    # 空页：整段 [range_start, range_end) 里库里的消息都没返回。
    await commit(factory, job, [], raw_count=0)

    assert await deleted_ids(factory) == {old: "reconciled"}


# ---- 清理（AC-21）-------------------------------------------------------------------


async def test_cleanup_removes_old_finished_jobs_and_old_quota_rows(
    factory: Factory, account_id: int
) -> None:
    async with factory() as session, session.begin():
        now = await session.scalar(select(func.now()))
        assert now is not None
        for status, age in (("done", 31), ("cancelled", 29), ("failed", 40), ("pending", 90)):
            session.add(
                HistoryJob(
                    batch_id=uuid.uuid4(),
                    channel_id=CHANNEL.id,
                    origin="manual",
                    range_start=h(0),
                    range_end=h(1),
                    status=status,
                    error="no_access" if status == "failed" else None,
                    finished_at=None if status == "pending" else now - timedelta(days=age),
                )
            )
        session.add(HistoryPageHour(hour=now - timedelta(hours=49), pages=3))
        session.add(HistoryPageHour(hour=now - timedelta(hours=47), pages=4))
        session.add(
            ChannelCoverage(channel_id=CHANNEL.id, span=Range(h(0), h(1)), source="history")
        )

    async with factory() as session, session.begin():
        result = await history_db.cleanup_history(session)

    assert (result.jobs, result.page_hours) == (2, 1)
    async with factory() as session:
        left = set(await session.scalars(select(HistoryJob.status)))
        assert left == {"cancelled", "pending"}
        assert len(list(await session.scalars(select(HistoryPageHour.hour)))) == 1
        assert len(list(await session.scalars(select(ChannelCoverage.id)))) == 1
