"""Snapshot builders and small read helpers shared by the db integration tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import upsert_channel, upsert_guild
from harvester.db.models import Attachment, DiscordUser, Message, MessageRevision
from harvester.db.snapshots import (
    AttachmentSnapshot,
    ChannelSnapshot,
    GuildSnapshot,
    MessageSnapshot,
    UserSnapshot,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


GUILD = GuildSnapshot(id=100, name="Signals", icon_hash="abc")
CATEGORY = ChannelSnapshot(id=200, guild_id=100, parent_id=None, type=4, name="Trading", position=0)
CHANNEL = ChannelSnapshot(id=201, guild_id=100, parent_id=200, type=0, name="alerts", position=1)

OTHER_GUILD = GuildSnapshot(id=110, name="Other", icon_hash=None)
OTHER_CHANNEL = ChannelSnapshot(
    id=211, guild_id=110, parent_id=None, type=0, name="general", position=0
)

USER = UserSnapshot(id=300, kind="user", username="kol", global_name="KOL", avatar_hash=None)
BOT = UserSnapshot(id=301, kind="bot", username="alertbot", global_name=None, avatar_hash=None)
WEBHOOK = UserSnapshot(
    id=302, kind="webhook", username="TradingView", global_name=None, avatar_hash=None
)

# A real snowflake, so `created_at` is a plausible 2024 time.
MESSAGE_ID = 1_300_000_000_000_000_000


def attachment(id: int, url: str = "https://cdn.example/a") -> AttachmentSnapshot:
    return AttachmentSnapshot(
        id=id,
        filename=f"file-{id}.png",
        content_type="image/png",
        size_bytes=1024,
        url=url,
        width=640,
        height=480,
    )


BASE_MESSAGE = MessageSnapshot(
    id=MESSAGE_ID,
    channel_id=CHANNEL.id,
    guild_id=CHANNEL.guild_id,
    author=USER,
    author_display_name="KOL",
    type=0,
    content="BTC long 65k",
    embeds=(),
    forwarded=None,
    reply_to_id=None,
    edited_at=None,
    attachments=(),
)


def message(**changes: Any) -> MessageSnapshot:
    return replace(BASE_MESSAGE, **changes)


async def seed_channel(session: AsyncSession) -> None:
    """Guild, category and the text channel every message test writes into."""
    await upsert_guild(session, GUILD)
    assert await upsert_channel(session, CATEGORY) == "ok"
    assert await upsert_channel(session, CHANNEL) == "ok"


async def seed_other_channel(session: AsyncSession) -> None:
    await upsert_guild(session, OTHER_GUILD)
    assert await upsert_channel(session, OTHER_CHANNEL) == "ok"


async def get_message(session: AsyncSession, message_id: int = MESSAGE_ID) -> Message:
    return (
        await session.scalars(
            select(Message)
            .where(Message.id == message_id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def count_messages(session: AsyncSession) -> int:
    return int(await session.scalar(text("SELECT count(*) FROM message")) or 0)


async def get_revisions(
    session: AsyncSession, message_id: int = MESSAGE_ID
) -> list[MessageRevision]:
    stmt = (
        select(MessageRevision)
        .where(MessageRevision.message_id == message_id)
        .order_by(MessageRevision.version)
    )
    return list(await session.scalars(stmt.execution_options(populate_existing=True)))


async def get_attachments(session: AsyncSession, message_id: int = MESSAGE_ID) -> list[Attachment]:
    stmt = select(Attachment).where(Attachment.message_id == message_id).order_by(Attachment.id)
    return list(await session.scalars(stmt.execution_options(populate_existing=True)))


async def get_user(session: AsyncSession, user_id: int) -> DiscordUser:
    return (
        await session.scalars(
            select(DiscordUser)
            .where(DiscordUser.id == user_id)
            .execution_options(populate_existing=True)
        )
    ).one()


async def transaction_now(session: AsyncSession) -> datetime:
    value = await session.scalar(text("SELECT now()"))
    assert isinstance(value, datetime)
    return value


async def make_account(session: AsyncSession) -> int:
    """数据库最多一个账号，返回它的 id。"""
    from harvester.db import account as accounts

    row = await accounts.create_account(session, "main", b"cipher", 1)
    assert not isinstance(row, str)
    return row.id
