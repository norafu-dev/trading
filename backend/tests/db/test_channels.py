"""AC-6、AC-14：管理页的读取和开关（真实 PostgreSQL）。"""

import asyncio
from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import account as accounts
from harvester.db import channel_sync, channels, raw
from harvester.db.channels import ChannelRow
from harvester.db.ingest import upsert_channel, upsert_guild
from harvester.db.models import AccountChannelAccess, Channel, DiscordAccount, Guild
from harvester.db.snapshots import ChannelSnapshot
from tests.db.builders import CATEGORY, CHANNEL, GUILD, at, make_account, seed_channel
from tests.db.test_channel_sync import POST, STATE, THREAD, VOICE


async def seeded(session: AsyncSession) -> int:
    account_id = await make_account(session)
    await channel_sync.apply_guild_state(session, account_id, STATE, at(0))
    await channel_sync.record_synced(session, account_id, 1, at(0))
    return account_id


async def test_enable_writes_flag_and_owner(session: AsyncSession) -> None:
    account_id = await seeded(session)

    result = await channels.set_collect(session, CHANNEL.id, True)

    assert isinstance(result, ChannelRow) and result.collect_enabled
    owner = await session.scalar(
        select(Channel.collector_account_id).where(Channel.id == CHANNEL.id)
    )
    assert owner == account_id


async def test_disable_keeps_owner(session: AsyncSession) -> None:
    account_id = await seeded(session)
    await channels.set_collect(session, CHANNEL.id, True)

    result = await channels.set_collect(session, CHANNEL.id, False)

    assert isinstance(result, ChannelRow) and not result.collect_enabled
    owner = await session.scalar(
        select(Channel.collector_account_id).where(Channel.id == CHANNEL.id)
    )
    assert owner == account_id


async def test_missing_channel(session: AsyncSession) -> None:
    assert await channels.set_collect(session, 999, True) == "channel_not_found"


async def test_enable_without_account(session: AsyncSession) -> None:
    await seed_channel(session)

    assert await channels.set_collect(session, CHANNEL.id, True) == "account_not_found"


async def test_disable_without_account_succeeds(session: AsyncSession) -> None:
    await seed_channel(session)

    result = await channels.set_collect(session, CHANNEL.id, False)

    assert isinstance(result, ChannelRow)


async def test_uncollectable_type(session: AsyncSession) -> None:
    await seeded(session)

    assert await channels.set_collect(session, VOICE.id, True) == "channel_not_collectable"


async def test_no_access_row_false(session: AsyncSession) -> None:
    await seeded(session)

    assert await channels.set_collect(session, POST.id, True) == "channel_no_access"


async def test_missing_row_after_sync_means_no_access(session: AsyncSession) -> None:
    await seeded(session)
    await session.execute(
        AccountChannelAccess.__table__.delete().where(  # pyright: ignore[reportAttributeAccessIssue]
            AccountChannelAccess.channel_id == CHANNEL.id
        )
    )

    assert await channels.set_collect(session, CHANNEL.id, True) == "channel_no_access"


async def test_never_synced_allows_enable(session: AsyncSession) -> None:  # AC-14
    await seed_channel(session)
    await make_account(session)

    result = await channels.set_collect(session, CHANNEL.id, True)

    assert isinstance(result, ChannelRow) and result.collect_enabled


async def test_deleted_channel_or_left_guild_is_unavailable(session: AsyncSession) -> None:
    await seeded(session)
    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status="deleted"))
    assert await channels.set_collect(session, CHANNEL.id, True) == "channel_unavailable"

    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status="active"))
    await session.execute(update(Guild).values(status="left"))
    assert await channels.set_collect(session, CHANNEL.id, True) == "channel_unavailable"


async def test_archived_thread_can_be_enabled(session: AsyncSession) -> None:
    await seeded(session)
    await session.execute(update(Channel).where(Channel.id == THREAD.id).values(status="archived"))

    result = await channels.set_collect(session, THREAD.id, True)

    assert isinstance(result, ChannelRow) and result.collect_enabled


async def test_real_change_notifies_and_same_value_does_not(engine: object) -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

    assert isinstance(engine, AsyncEngine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        await seeded(session)
    received: list[str] = []

    def on_notify(_conn: object, _pid: int, channel: str, _payload: object) -> None:
        received.append(channel)

    conn = await raw.connect()
    try:
        await conn.add_listener(raw.CHANNEL_COLLECT_CHANGED_CHANNEL, on_notify)
        await conn.add_listener(raw.CHANNELS_CHANGED_CHANNEL, on_notify)
        async with factory() as session, session.begin():
            await channels.set_collect(session, CHANNEL.id, True)
        await asyncio.sleep(0.1)
        assert sorted(received) == sorted(
            [raw.CHANNELS_CHANGED_CHANNEL, raw.CHANNEL_COLLECT_CHANGED_CHANNEL]
        )

        received.clear()
        async with factory() as session, session.begin():
            await channels.set_collect(session, CHANNEL.id, True)
        await asyncio.sleep(0.1)
        assert received == []
    finally:
        await conn.close()


async def test_switch_identity_clears_sync_time_and_keeps_switches(session: AsyncSession) -> None:
    # AC-14
    account_id = await seeded(session)
    await channels.set_collect(session, CHANNEL.id, True)
    await accounts.record_status(session, account_id, 1, "account_mismatch", "account_mismatch")

    await accounts.switch_identity(session)

    assert await session.scalar(select(DiscordAccount.channels_synced_at)) is None
    assert await session.scalar(select(Channel.collect_enabled).where(Channel.id == CHANNEL.id))
    # 访问行被清空后，从未同步的放行规则生效。
    result = await channels.set_collect(session, THREAD.id, True)
    assert isinstance(result, ChannelRow)


async def test_list_guilds_counts_enabled_and_sorts_left_last(session: AsyncSession) -> None:
    await seeded(session)
    await upsert_guild(session, replace(GUILD, id=50, name="Zed"))
    await upsert_guild(session, replace(GUILD, id=51, name="Aaa"))
    await session.execute(update(Guild).where(Guild.id == 51).values(status="left"))
    await channels.set_collect(session, CHANNEL.id, True)
    # 失去权限但仍开启的频道也算进去。
    await session.execute(update(AccountChannelAccess).values(can_read=False))

    rows = await channels.list_guilds(session)

    assert [(g.name, g.enabled_count) for g in rows] == [("Signals", 1), ("Zed", 0), ("Aaa", 0)]


async def test_list_channels_filters_voice_deleted_and_unenabled_archived(
    session: AsyncSession,
) -> None:
    await seeded(session)
    await session.execute(update(Channel).where(Channel.id == POST.id).values(status="archived"))
    await upsert_channel(
        session, ChannelSnapshot(id=206, guild_id=100, parent_id=200, type=0, name="x", position=8)
    )
    await session.execute(update(Channel).where(Channel.id == 206).values(status="deleted"))

    rows = await channels.list_guild_channels(session, GUILD.id)

    assert not isinstance(rows, str)
    assert {c.id for c in rows} == {CATEGORY.id, CHANNEL.id, 202, THREAD.id}
    by_id = {c.id: c for c in rows}
    assert by_id[CHANNEL.id].can_read and by_id[CHANNEL.id].collectable
    assert not by_id[CATEGORY.id].collectable and not by_id[CATEGORY.id].can_read


async def test_enabled_archived_thread_stays_listed(session: AsyncSession) -> None:
    await seeded(session)
    await channels.set_collect(session, THREAD.id, True)
    await session.execute(update(Channel).where(Channel.id == THREAD.id).values(status="archived"))

    rows = await channels.list_guild_channels(session, GUILD.id)

    assert not isinstance(rows, str)
    assert THREAD.id in {c.id for c in rows}


async def test_list_channels_of_unknown_guild(session: AsyncSession) -> None:
    assert await channels.list_guild_channels(session, 404) == "guild_not_found"


async def test_channel_without_access_row_reads_as_no_access(session: AsyncSession) -> None:  # AC-3
    account_id = await make_account(session)
    state = replace(STATE, channels=(CATEGORY, CHANNEL), access=())
    await channel_sync.apply_guild_state(session, account_id, state, at(0))

    rows = await channels.list_guild_channels(session, GUILD.id)

    assert not isinstance(rows, str)
    assert not {c.id: c for c in rows}[CHANNEL.id].can_read
