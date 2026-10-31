"""Guild, channel and user upserts plus status changes (real PostgreSQL)."""

from dataclasses import replace

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import (
    ingest_message,
    set_channel_status,
    set_guild_status,
    upsert_channel,
    upsert_guild,
    upsert_user,
)
from harvester.db.models import Channel, Guild
from harvester.db.snapshots import COLLECTABLE_CHANNEL_TYPES
from tests.db.builders import (
    CATEGORY,
    CHANNEL,
    GUILD,
    USER,
    WEBHOOK,
    at,
    get_message,
    get_user,
    message,
    seed_channel,
    transaction_now,
)


async def get_channel(session: AsyncSession, channel_id: int = CHANNEL.id) -> Channel:
    return (
        await session.scalars(
            select(Channel)
            .where(Channel.id == channel_id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def get_guild(session: AsyncSession) -> Guild:
    return (
        await session.scalars(
            select(Guild).where(Guild.id == GUILD.id).execution_options(populate_existing=True)
        )
    ).one()


# --- upsert_guild / upsert_channel ------------------------------------------------


async def test_upsert_guild_inserts_then_updates_name_and_icon(session: AsyncSession) -> None:
    await upsert_guild(session, GUILD)
    await upsert_guild(session, replace(GUILD, name="Renamed", icon_hash=None))

    guild = await get_guild(session)
    assert (guild.name, guild.icon_hash, guild.status) == ("Renamed", None, "active")


async def test_first_seen_at_is_the_database_time_not_discord_time(session: AsyncSession) -> None:
    await upsert_guild(session, GUILD)

    assert (await get_guild(session)).first_seen_at == await transaction_now(session)


async def test_upsert_channel_returns_missing_guild_without_writing(  # covers: AC-12
    session: AsyncSession,
) -> None:
    assert await upsert_channel(session, CHANNEL) == "missing_guild"
    assert await session.scalar(text("SELECT count(*) FROM channel")) == 0


async def test_upsert_channel_returns_missing_parent_without_writing(  # covers: AC-12
    session: AsyncSession,
) -> None:
    await upsert_guild(session, GUILD)

    assert await upsert_channel(session, CHANNEL) == "missing_parent"
    assert await session.scalar(text("SELECT count(*) FROM channel")) == 0


async def test_upsert_channel_stores_category_and_child(session: AsyncSession) -> None:
    await seed_channel(session)

    channel = await get_channel(session)
    assert (channel.parent_id, channel.type, channel.name, channel.position) == (
        200,
        0,
        "alerts",
        1,
    )
    assert channel.collect_enabled is False
    assert channel.status == "active"


async def test_upsert_channel_never_overwrites_switch_or_status(session: AsyncSession) -> None:
    await seed_channel(session)
    await session.execute(
        update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=True)
    )
    await set_channel_status(session, CHANNEL.id, "archived", at(1))

    result = await upsert_channel(session, replace(CHANNEL, name="alerts-v2", parent_id=None))

    channel = await get_channel(session)
    assert result == "ok"
    assert (channel.name, channel.parent_id) == ("alerts-v2", None)
    assert (channel.collect_enabled, channel.status) == (True, "archived")


async def test_category_cannot_be_enabled_for_collection(session: AsyncSession) -> None:  # AC-1
    await seed_channel(session)

    with pytest.raises(IntegrityError, match="ck_channel_collectable_type"):
        await session.execute(
            update(Channel).where(Channel.id == CATEGORY.id).values(collect_enabled=True)
        )


async def test_collectable_constraint_matches_the_python_constant(  # covers: AC-1
    session: AsyncSession,
) -> None:
    await upsert_guild(session, GUILD)
    allowed: list[int] = []
    for channel_type in range(25):
        channel_id = 1000 + channel_type
        snap = replace(CATEGORY, id=channel_id, type=channel_type, name=f"t{channel_type}")
        assert await upsert_channel(session, snap) == "ok"
        try:
            async with session.begin_nested():
                await session.execute(
                    update(Channel).where(Channel.id == channel_id).values(collect_enabled=True)
                )
            allowed.append(channel_type)
        except IntegrityError:
            pass

    assert tuple(allowed) == COLLECTABLE_CHANNEL_TYPES


# --- set_guild_status / set_channel_status ----------------------------------------


async def test_set_channel_status_reports_changed_unchanged_and_not_found(  # covers: AC-8
    session: AsyncSession,
) -> None:
    await seed_channel(session)

    assert await set_channel_status(session, CHANNEL.id, "deleted", at(1)) == "changed"
    assert await set_channel_status(session, CHANNEL.id, "deleted", at(2)) == "unchanged"
    assert await set_channel_status(session, 999, "deleted", at(3)) == "not_found"
    assert (await get_channel(session)).status_changed_at == at(1)


async def test_deleting_a_channel_leaves_its_messages_untouched(  # covers: AC-8
    session: AsyncSession,
) -> None:
    await seed_channel(session)
    await ingest_message(session, message(), "realtime", at(0))

    await set_channel_status(session, CHANNEL.id, "deleted", at(1))

    stored = await get_message(session)
    assert (stored.deleted_at, stored.delete_source) == (None, None)


async def test_set_guild_status_reports_changed_unchanged_and_not_found(  # covers: AC-8
    session: AsyncSession,
) -> None:
    await upsert_guild(session, GUILD)

    assert await set_guild_status(session, GUILD.id, "left", at(1)) == "changed"
    assert await set_guild_status(session, GUILD.id, "left", at(2)) == "unchanged"
    assert await set_guild_status(session, 999, "left", at(3)) == "not_found"
    guild = await get_guild(session)
    assert (guild.status, guild.status_changed_at) == ("left", at(1))


async def test_upsert_guild_never_overwrites_status(session: AsyncSession) -> None:
    await upsert_guild(session, GUILD)
    await set_guild_status(session, GUILD.id, "left", at(1))

    await upsert_guild(session, replace(GUILD, name="Back"))

    assert (await get_guild(session)).status == "left"


async def test_channel_with_messages_cannot_be_hard_deleted(  # covers: AC-8
    session: AsyncSession,
) -> None:
    await seed_channel(session)
    await ingest_message(session, message(), "realtime", at(0))

    with pytest.raises(IntegrityError, match="fk_message_channel_id_guild_id_channel"):
        await session.execute(text("DELETE FROM channel WHERE id = :id"), {"id": CHANNEL.id})


# --- upsert_user ------------------------------------------------------------------


async def test_upsert_user_updates_names_but_never_kind(session: AsyncSession) -> None:  # AC-3
    await upsert_user(session, USER)
    await upsert_user(session, replace(USER, kind="bot", username="kol2", global_name="New"))

    user = await get_user(session, USER.id)
    assert (user.kind, user.username, user.global_name) == ("user", "kol2", "New")


async def test_upsert_user_never_updates_a_webhook(session: AsyncSession) -> None:  # AC-3
    await upsert_user(session, WEBHOOK)
    await upsert_user(session, replace(WEBHOOK, username="Renamed hook", avatar_hash="zzz"))

    user = await get_user(session, WEBHOOK.id)
    assert (user.username, user.avatar_hash) == ("TradingView", None)


async def test_upsert_user_leaves_updated_at_alone_when_nothing_changed(
    session: AsyncSession,
) -> None:
    await upsert_user(session, USER)
    await session.commit()
    first = (await get_user(session, USER.id)).updated_at
    await session.commit()

    await upsert_user(session, USER)

    assert (await get_user(session, USER.id)).updated_at == first
