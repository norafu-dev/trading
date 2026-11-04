"""spec 0010：已读位置和未读数（真实 PostgreSQL）。"""

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import channel_sync, channels, read_state
from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.models import Channel, ChannelReadState, DiscordAccount
from harvester.db.snapshots import UserSnapshot
from tests.db.builders import (
    BOT,
    CATEGORY,
    CHANNEL,
    MESSAGE_ID,
    USER,
    at,
    make_account,
    message,
    seed_channel,
    seed_other_channel,
)
from tests.db.test_channel_sync import STATE, THREAD

M1, M2, M3 = MESSAGE_ID + 1, MESSAGE_ID + 2, MESSAGE_ID + 3


async def add(session: AsyncSession, message_id: int, author: UserSnapshot = USER) -> None:
    await ingest_message(
        session, message(id=message_id, author=author, author_display_name="x"), "realtime", at(0)
    )


async def enable(session: AsyncSession, *channel_ids: int) -> None:
    await session.execute(
        update(Channel).where(Channel.id.in_(channel_ids)).values(collect_enabled=True)
    )


async def position(session: AsyncSession, channel_id: int) -> int | None:
    return await session.scalar(
        select(ChannelReadState.last_read_message_id).where(
            ChannelReadState.channel_id == channel_id
        )
    )


async def counts(session: AsyncSession) -> dict[int, int]:
    return {u.channel_id: u.count for u in await read_state.list_unread(session)}


async def test_migration_backfill(session: AsyncSession) -> None:  # AC-1
    """回填语句：已开启和有消息的频道各一行，没有消息的开启频道为 0，其余没有行。"""
    await seed_channel(session)
    await seed_other_channel(session)
    await add(session, M1)
    await add(session, M2)
    await enable(session, 211)
    backfill = (
        "INSERT INTO channel_read_state (channel_id, last_read_message_id) "
        "SELECT c.id, COALESCE(MAX(m.id), 0) FROM channel c "
        "LEFT JOIN message m ON m.channel_id = c.id GROUP BY c.id "
        "HAVING c.collect_enabled OR MAX(m.id) IS NOT NULL"
    )

    await session.execute(text(backfill))

    assert await position(session, CHANNEL.id) == M2
    assert await position(session, 211) == 0
    assert await position(session, CATEGORY.id) is None


async def test_enable_creates_row_at_latest_message(session: AsyncSession) -> None:  # AC-2
    await make_account(session)
    await channel_sync.apply_guild_state(session, 1, STATE, at(0))
    await channel_sync.record_synced(session, 1, 1, at(0))
    await add(session, M1)

    await channels.set_collect(session, CHANNEL.id, True)

    assert await position(session, CHANNEL.id) == M1
    assert await counts(session) == {}


async def test_enable_keeps_existing_position(session: AsyncSession) -> None:  # AC-2
    await make_account(session)
    await channel_sync.apply_guild_state(session, 1, STATE, at(0))
    await channel_sync.record_synced(session, 1, 1, at(0))
    await channels.set_collect(session, CHANNEL.id, True)
    await add(session, M1)
    await channels.set_collect(session, CHANNEL.id, False)
    await add(session, M2)

    await channels.set_collect(session, CHANNEL.id, True)

    assert await position(session, CHANNEL.id) == 0
    assert await counts(session) == {CHANNEL.id: 2}


async def test_category_enable_creates_rows(session: AsyncSession) -> None:  # AC-2
    await make_account(session)
    await channel_sync.apply_guild_state(session, 1, STATE, at(0))
    await channel_sync.record_synced(session, 1, 1, at(0))
    await add(session, M1)

    result = await channels.set_category_collect(session, CATEGORY.id, True)

    assert not isinstance(result, str) and result.changed >= 1
    assert await position(session, CHANNEL.id) == M1


async def test_counts_new_messages_after_position(session: AsyncSession) -> None:  # AC-3
    await seed_channel(session)
    await enable(session, CHANNEL.id)
    await add(session, M1)
    await channels.init_read_state(session, [CHANNEL.id])
    await add(session, M2)
    await add(session, M3)

    assert await counts(session) == {CHANNEL.id: 2}


async def test_deleted_messages_not_counted(session: AsyncSession) -> None:  # AC-3
    await seed_channel(session)
    await enable(session, CHANNEL.id)
    await channels.init_read_state(session, [CHANNEL.id])
    await add(session, M1)
    await add(session, M2)
    await mark_messages_deleted(session, [M1], at(1), "realtime")

    assert await counts(session) == {CHANNEL.id: 1}


async def test_own_messages_not_counted(session: AsyncSession) -> None:  # AC-3
    account_id = await make_account(session)
    await session.execute(
        update(DiscordAccount)
        .where(DiscordAccount.id == account_id)
        .values(discord_user_id=USER.id)
    )
    await seed_channel(session)
    await enable(session, CHANNEL.id)
    await channels.init_read_state(session, [CHANNEL.id])
    await add(session, M1, USER)
    await add(session, M2, BOT)

    assert await counts(session) == {CHANNEL.id: 1}


async def test_unknown_account_user_counts_everything(session: AsyncSession) -> None:  # AC-3
    await make_account(session)
    await seed_channel(session)
    await enable(session, CHANNEL.id)
    await channels.init_read_state(session, [CHANNEL.id])
    await add(session, M1, USER)
    await add(session, M2, BOT)

    assert await counts(session) == {CHANNEL.id: 2}


async def test_count_capped_at_100(session: AsyncSession) -> None:  # AC-3
    await seed_channel(session)
    await enable(session, CHANNEL.id)
    await channels.init_read_state(session, [CHANNEL.id])
    for i in range(1, 103):
        await add(session, MESSAGE_ID + i)

    assert await counts(session) == {CHANNEL.id: read_state.UNREAD_CAP}


async def test_disabled_deleted_and_rowless_channels_hidden(session: AsyncSession) -> None:  # AC-3
    await seed_channel(session)
    await seed_other_channel(session)
    await enable(session, CHANNEL.id, 211)
    await channels.init_read_state(session, [CHANNEL.id, 211])
    await add(session, M1)
    await session.execute(update(Channel).where(Channel.id == 211).values(status="deleted"))
    await session.execute(text("DELETE FROM channel_read_state WHERE channel_id = 211"))
    assert await counts(session) == {CHANNEL.id: 1}

    await session.execute(
        update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=False)
    )
    assert await counts(session) == {}


async def test_thread_counts_separately(session: AsyncSession) -> None:  # AC-3
    await make_account(session)
    await channel_sync.apply_guild_state(session, 1, STATE, at(0))
    await enable(session, CHANNEL.id, THREAD.id)
    await channels.init_read_state(session, [CHANNEL.id, THREAD.id])
    await add(session, M1)
    await ingest_message(
        session,
        message(id=M2, channel_id=THREAD.id, guild_id=THREAD.guild_id),
        "realtime",
        at(0),
    )

    assert await counts(session) == {CHANNEL.id: 1, THREAD.id: 1}


async def test_mark_read_advances_never_goes_back(session: AsyncSession) -> None:  # AC-4
    await seed_channel(session)
    await channels.init_read_state(session, [CHANNEL.id])
    for m in (M1, M2, M3):
        await add(session, m)

    assert await read_state.mark_read(session, CHANNEL.id, M2) == M2
    assert await read_state.mark_read(session, CHANNEL.id, M1) == M2
    assert await read_state.mark_read(session, CHANNEL.id, M2) == M2
    assert await position(session, CHANNEL.id) == M2


async def test_mark_read_clamped_to_latest(session: AsyncSession) -> None:  # AC-4
    await seed_channel(session)
    await add(session, M1)

    assert await read_state.mark_read(session, CHANNEL.id, 2**63 - 1) == M1


async def test_mark_read_creates_missing_row(session: AsyncSession) -> None:  # AC-4
    await seed_channel(session)
    await add(session, M1)

    assert await position(session, CHANNEL.id) is None
    assert await read_state.mark_read(session, CHANNEL.id, M1) == M1


async def test_mark_read_unknown_channel(session: AsyncSession) -> None:  # AC-4
    assert await read_state.mark_read(session, 999, 1) == "channel_not_found"
