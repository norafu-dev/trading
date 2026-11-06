"""spec 0012 AC-15、AC-16：缺口计算、一键补和自动补短缺口（真实 PostgreSQL）。"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.db import history as history_db
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    HistoryJob,
)
from tests.db.builders import CHANNEL, make_account, seed_channel

Factory = async_sessionmaker[AsyncSession]
DAY = datetime(2026, 1, 1, tzinfo=UTC)


def h(hours: float) -> datetime:
    return DAY + timedelta(hours=hours)


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


async def add_coverage(factory: Factory, *spans: tuple[datetime, datetime | None]) -> None:
    async with factory() as session, session.begin():
        for lo, hi in spans:
            session.add(
                ChannelCoverage(channel_id=CHANNEL.id, span=Range(lo, hi), source="realtime")
            )


async def gaps(factory: Factory) -> list[tuple[datetime, datetime]]:
    async with factory() as session:
        rows = await history_db.channel_gaps(session)
    return [gap for row in rows for gap in row.gaps]


async def job_rows(factory: Factory) -> list[tuple[datetime, datetime, str]]:
    async with factory() as session:
        rows = await session.execute(
            select(HistoryJob.range_start, HistoryJob.range_end, HistoryJob.origin).order_by(
                HistoryJob.range_start
            )
        )
    return [(r.range_start, r.range_end, r.origin) for r in rows]


async def set_auto_gap_max_hours(factory: Factory, hours: int) -> None:
    async with factory() as session, session.begin():
        settings = await history_db.get_history_settings(session)
        await history_db.update_history_settings(
            session,
            page_delay_min_s=settings.page_delay_min_s,
            page_delay_max_s=settings.page_delay_max_s,
            daily_page_limit=settings.daily_page_limit,
            run_minutes=settings.run_minutes,
            rest_minutes=settings.rest_minutes,
            auto_gap_max_hours=hours,
        )


# covers: AC-15
async def test_gap_between_two_segments(factory: Factory, account_id: int) -> None:
    await add_coverage(factory, (h(1), h(3)), (h(5), None))

    assert await gaps(factory) == [(h(3), h(5))]


# covers: AC-15
async def test_single_open_segment_has_no_gap(factory: Factory, account_id: int) -> None:
    await add_coverage(factory, (h(1), None))

    assert await gaps(factory) == []


# covers: AC-15
async def test_deleted_or_unreadable_channel_is_not_listed(
    factory: Factory, account_id: int
) -> None:
    await add_coverage(factory, (h(1), h(3)), (h(5), h(6)))
    assert await gaps(factory) == [(h(3), h(5))]

    async with factory() as session, session.begin():
        await session.execute(
            update(AccountChannelAccess)
            .where(AccountChannelAccess.channel_id == CHANNEL.id)
            .values(can_read=False)
        )
    assert await gaps(factory) == []

    async with factory() as session, session.begin():
        await session.execute(
            update(AccountChannelAccess)
            .where(AccountChannelAccess.channel_id == CHANNEL.id)
            .values(can_read=True)
        )
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(status="deleted")
        )
    assert await gaps(factory) == []


# covers: AC-15
async def test_fill_creates_one_job_per_gap_and_subtracts_open_jobs(
    factory: Factory, account_id: int
) -> None:
    await add_coverage(factory, (h(1), h(3)), (h(5), h(7)), (h(9), None))
    async with factory() as session, session.begin():
        await history_db.create_jobs(
            session, (CHANNEL.id,), h(7), h(8), refetch=False, origin="manual"
        )

    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), origin="gap_manual")

    assert len(result.job_ids) == 2
    assert await job_rows(factory) == [
        (h(3), h(5), "gap_manual"),
        (h(7), h(8), "manual"),
        (h(8), h(9), "gap_manual"),
    ]


# covers: AC-15
async def test_fill_without_gaps_reports_nothing_to_fetch(
    factory: Factory, account_id: int
) -> None:
    await add_coverage(factory, (h(1), None))

    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), origin="gap_manual")

    assert result.job_ids == ()
    assert [s.reason for s in result.skipped] == ["nothing_to_fetch"]


# covers: AC-16
async def test_auto_fills_only_the_adjacent_short_gap(factory: Factory, account_id: int) -> None:
    # 一段 2 小时的缺口紧挨着新打开的实时段，另一段更早的缺口不碰。
    await add_coverage(factory, (h(0), h(1)), (h(3), h(5)), (h(7), None))

    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), h(7), origin="gap_auto")

    assert len(result.job_ids) == 1
    assert await job_rows(factory) == [(h(5), h(7), "gap_auto")]


# covers: AC-16
async def test_auto_fills_a_five_second_gap(factory: Factory, account_id: int) -> None:
    at = h(1) + timedelta(seconds=5)
    await add_coverage(factory, (h(0), h(1)), (at, None))

    async with factory() as session, session.begin():
        await history_db.create_gap_jobs(session, (CHANNEL.id,), at, origin="gap_auto")

    assert await job_rows(factory) == [(h(1), at, "gap_auto")]


# covers: AC-16
async def test_auto_skips_gaps_over_the_limit_and_when_disabled(
    factory: Factory, account_id: int
) -> None:
    await add_coverage(factory, (h(0), h(1)), (h(11), None))

    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), h(11), origin="gap_auto")
    assert result.job_ids == ()

    await set_auto_gap_max_hours(factory, 12)
    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), h(11), origin="gap_auto")
    assert await job_rows(factory) == [(h(1), h(11), "gap_auto")]

    # 关掉自动补（0）之后，连紧挨着的短缺口也不建。
    async with factory() as session, session.begin():
        await session.execute(update(HistoryJob).values(status="cancelled"))
    await set_auto_gap_max_hours(factory, 0)
    async with factory() as session, session.begin():
        result = await history_db.create_gap_jobs(session, (CHANNEL.id,), h(11), origin="gap_auto")
    assert result.job_ids == ()


# covers: AC-15
async def test_gap_summary_counts_channels_and_total(factory: Factory, account_id: int) -> None:
    await add_coverage(factory, (h(1), h(3)), (h(5), h(6)), (h(9), None))

    async with factory() as session:
        rows = await history_db.gap_summary(session, CHANNEL.id)
        assert await history_db.gap_channel_count(session) == 1

    assert len(rows) == 1
    row = rows[0]
    assert row.channel_id == CHANNEL.id
    assert row.channel_name == CHANNEL.name
    assert row.gaps == ((h(3), h(5)), (h(6), h(9)))
    assert row.total_seconds == (2 + 3) * 3600
