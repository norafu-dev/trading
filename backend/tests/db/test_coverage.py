"""Realtime coverage segments: open, close and crash recovery (real PostgreSQL)."""

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import (
    close_realtime_coverage,
    close_stale_coverage,
    open_realtime_coverage,
    upsert_channel,
)
from harvester.db.models import Channel, ChannelCoverage, DiscordAccount
from tests.db.builders import CHANNEL, at, seed_channel

SECOND = replace(CHANNEL, id=202, name="second")
THIRD = replace(CHANNEL, id=203, name="third")


@pytest.fixture(autouse=True)
async def channels(session: AsyncSession) -> None:
    await seed_channel(session)
    assert await upsert_channel(session, SECOND) == "ok"
    assert await upsert_channel(session, THIRD) == "ok"


async def spans(session: AsyncSession, channel_id: int = CHANNEL.id) -> list[Range[datetime]]:
    stmt = (
        select(ChannelCoverage.span)
        .where(ChannelCoverage.channel_id == channel_id)
        .order_by(ChannelCoverage.id)
    )
    return list(await session.scalars(stmt.execution_options(populate_existing=True)))


async def add_account(session: AsyncSession, label: str, last_heartbeat_at: datetime | None) -> int:
    account_id = await session.scalar(
        insert(DiscordAccount)
        .values(
            label=label,
            token_ciphertext=b"\x00",
            token_key_version=1,
            last_heartbeat_at=last_heartbeat_at,
        )
        .returning(DiscordAccount.id)
    )
    assert account_id is not None
    return account_id


async def assign(
    session: AsyncSession, channel_id: int, account_id: int | None, enabled: bool = True
) -> None:
    await session.execute(
        update(Channel)
        .where(Channel.id == channel_id)
        .values(collector_account_id=account_id, collect_enabled=enabled)
    )


# --- open / close -----------------------------------------------------------------


async def test_open_twice_keeps_a_single_open_segment(session: AsyncSession) -> None:  # AC-10
    first = await open_realtime_coverage(session, CHANNEL.id, at(0))
    second = await open_realtime_coverage(session, CHANNEL.id, at(5))

    assert (first, second) == ("opened", "already_open")
    assert await spans(session) == [Range(at(0), None, bounds="[)")]


async def test_close_sets_the_upper_bound(session: AsyncSession) -> None:  # covers: AC-10
    await open_realtime_coverage(session, CHANNEL.id, at(0))

    result = await close_realtime_coverage(session, CHANNEL.id, at(10))

    assert result == "closed"
    assert await spans(session) == [Range(at(0), at(10), bounds="[)")]


@pytest.mark.parametrize("close_minute", [-5, 0])
async def test_close_at_or_before_start_discards_the_segment(  # covers: AC-10
    session: AsyncSession, close_minute: int
) -> None:
    await open_realtime_coverage(session, CHANNEL.id, at(0))

    result = await close_realtime_coverage(session, CHANNEL.id, at(close_minute))

    assert result == "discarded_empty"
    assert await spans(session) == []


async def test_close_without_an_open_segment_is_not_open(session: AsyncSession) -> None:  # AC-10
    await open_realtime_coverage(session, CHANNEL.id, at(0))
    await close_realtime_coverage(session, CHANNEL.id, at(10))

    assert await close_realtime_coverage(session, CHANNEL.id, at(20)) == "not_open"


async def test_reopen_after_close_starts_a_new_segment(session: AsyncSession) -> None:
    await open_realtime_coverage(session, CHANNEL.id, at(0))
    await close_realtime_coverage(session, CHANNEL.id, at(10))

    assert await open_realtime_coverage(session, CHANNEL.id, at(20)) == "opened"
    assert await spans(session) == [
        Range(at(0), at(10), bounds="[)"),
        Range(at(20), None, bounds="[)"),
    ]


async def test_segments_are_per_channel(session: AsyncSession) -> None:
    assert await open_realtime_coverage(session, CHANNEL.id, at(0)) == "opened"
    assert await open_realtime_coverage(session, SECOND.id, at(0)) == "opened"


async def test_database_rejects_an_empty_span(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_channel_coverage_span_not_empty"):
        await session.execute(
            insert(ChannelCoverage).values(
                channel_id=CHANNEL.id, span=Range(at(0), at(0), bounds="[)"), source="history"
            )
        )


# --- close_stale_coverage ---------------------------------------------------------


async def test_crash_recovery_closes_at_heartbeat_and_drops_later_segments(  # covers: AC-10
    session: AsyncSession,
) -> None:
    account = await add_account(session, "main", last_heartbeat_at=at(10))
    await assign(session, CHANNEL.id, account)
    await assign(session, SECOND.id, None)  # unowned
    await open_realtime_coverage(session, CHANNEL.id, at(0))  # before the heartbeat
    await open_realtime_coverage(session, SECOND.id, at(15))  # after the heartbeat

    closed = await close_stale_coverage(session, account)

    assert closed == 2
    assert await spans(session, CHANNEL.id) == [Range(at(0), at(10), bounds="[)")]
    assert await spans(session, SECOND.id) == []


async def test_crash_recovery_without_heartbeat_drops_open_segments(  # value sourcing
    session: AsyncSession,
) -> None:
    account = await add_account(session, "main", last_heartbeat_at=None)
    await assign(session, CHANNEL.id, account)
    await open_realtime_coverage(session, CHANNEL.id, at(0))

    assert await close_stale_coverage(session, account) == 1
    assert await spans(session) == []


async def test_crash_recovery_closes_disabled_channels_of_other_accounts(
    session: AsyncSession,
) -> None:
    # 这一版最多一个账号，但覆盖段的规则按多账号写（spec 0002）；只在这个事务里去掉限制。
    await session.execute(text("DROP INDEX uq_discord_account_single"))
    mine = await add_account(session, "main", last_heartbeat_at=at(10))
    other = await add_account(session, "other", last_heartbeat_at=at(30))
    await assign(session, CHANNEL.id, other, enabled=False)
    await open_realtime_coverage(session, CHANNEL.id, at(0))

    assert await close_stale_coverage(session, mine) == 1
    assert await spans(session) == [Range(at(0), at(10), bounds="[)")]


async def test_crash_recovery_leaves_other_accounts_active_segments_open(
    session: AsyncSession,
) -> None:
    # 这一版最多一个账号，但覆盖段的规则按多账号写（spec 0002）；只在这个事务里去掉限制。
    await session.execute(text("DROP INDEX uq_discord_account_single"))
    mine = await add_account(session, "main", last_heartbeat_at=at(10))
    other = await add_account(session, "other", last_heartbeat_at=at(30))
    await assign(session, THIRD.id, other)
    await open_realtime_coverage(session, THIRD.id, at(0))

    assert await close_stale_coverage(session, mine) == 0
    assert await spans(session, THIRD.id) == [Range(at(0), None, bounds="[)")]


async def test_crash_recovery_ignores_closed_and_history_segments(session: AsyncSession) -> None:
    account = await add_account(session, "main", last_heartbeat_at=at(10))
    await assign(session, CHANNEL.id, account)
    await open_realtime_coverage(session, CHANNEL.id, at(0))
    await close_realtime_coverage(session, CHANNEL.id, at(5))
    await session.execute(
        insert(ChannelCoverage).values(
            channel_id=CHANNEL.id, span=Range(at(-60), at(-30), bounds="[)"), source="history"
        )
    )

    assert await close_stale_coverage(session, account) == 0
