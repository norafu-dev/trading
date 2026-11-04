"""spec 0012 的数据库函数：建任务的范围扣减、领取顺序、提交一页、失败和放回（真实 PostgreSQL）。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.db import history as history_db
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
from tests.db.builders import CHANNEL, make_account, message, seed_channel, seed_other_channel

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
