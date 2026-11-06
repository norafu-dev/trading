"""频道消息导出（spec 0014）：只读快照、两端解析、封顶计数和按批取数。

函数都不提交事务；调用方用 `snapshot_options` 开一个 `REPEATABLE READ READ ONLY` 事务，
再调用 `apply_export_timeouts`，之后的查询共用同一个快照。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.models import Attachment, Channel, Guild, Media, Message, MessageRevision
from harvester.db.snapshots import EmbedData, ForwardedData, MediaKind, MediaStatus

# 一次导出最多这么多条；预览和下载两处都检查（测试里可以改小）。
EXPORT_MAX_MESSAGES = 100_000
BATCH_SIZE = 500
STATEMENT_TIMEOUT_MS = 30_000
IDLE_IN_TRANSACTION_TIMEOUT_MS = 300_000

# 传给 `session.connection(execution_options=...)`，必须在事务里第一条查询之前。
SNAPSHOT_OPTIONS: Mapping[str, Any] = {
    "isolation_level": "REPEATABLE READ",
    "postgresql_readonly": True,
}

_EXPORT_MEDIA_KINDS: tuple[MediaKind, ...] = (
    "attachment",
    "forwarded_attachment",
    "embed_image",
    "embed_thumbnail",
)


@dataclass(frozen=True)
class ExportChannel:
    id: int
    name: str
    type: int
    guild_id: int
    guild_name: str


@dataclass(frozen=True)
class Endpoint:
    id: int
    created_at: datetime


@dataclass(frozen=True)
class ExportRange:
    """已经落到具体消息上的范围，包含两端。"""

    start: Endpoint
    end: Endpoint


@dataclass(frozen=True)
class ExportAttachment:
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    width: int | None
    height: int | None
    removed_at: datetime | None


@dataclass(frozen=True)
class ExportMedia:
    id: int
    kind: MediaKind
    attachment_id: int | None
    slot: int | None
    status: MediaStatus
    declared_content_type: str | None
    declared_size_bytes: int | None


@dataclass(frozen=True)
class ExportRevision:
    version: int
    content: str
    embeds: tuple[EmbedData, ...]
    replaced_at: datetime


@dataclass(frozen=True)
class ExportMessage:
    """一条要写进文件的消息，加上它的附件、媒体行和旧版本。"""

    id: int
    channel_id: int
    guild_id: int
    author_id: int
    author_name: str
    type: int
    content: str
    embeds: tuple[EmbedData, ...]
    forwarded: ForwardedData | None
    reply_to_id: int | None
    version: int
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None
    attachments: tuple[ExportAttachment, ...] = ()
    media: tuple[ExportMedia, ...] = ()
    revisions: tuple[ExportRevision, ...] = ()


async def apply_export_timeouts(session: AsyncSession) -> None:
    """单条查询 30 秒；下载卡住 5 分钟不动就放掉快照（AC-20）。只对当前事务有效。"""
    await session.execute(text(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}"))
    await session.execute(
        text(f"SET LOCAL idle_in_transaction_session_timeout = {IDLE_IN_TRANSACTION_TIMEOUT_MS}")
    )


async def get_export_channel(session: AsyncSession, channel_id: int) -> ExportChannel | None:
    row = (
        await session.execute(
            select(Channel.id, Channel.name, Channel.type, Channel.guild_id, Guild.name)
            .join(Guild, Guild.id == Channel.guild_id)
            .where(Channel.id == channel_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return ExportChannel(row[0], row[1], row[2], row[3], row[4])


async def _endpoint(session: AsyncSession, channel_id: int, message_id: int) -> Endpoint | None:
    row = (
        await session.execute(
            select(Message.id, Message.created_at).where(
                Message.id == message_id, Message.channel_id == channel_id
            )
        )
    ).one_or_none()
    return Endpoint(row[0], row[1]) if row is not None else None


async def _edge(session: AsyncSession, channel_id: int, newest: bool) -> Endpoint | None:
    """频道里最早（或最新）的一条，走 (channel_id, id DESC) 索引。"""
    order = Message.id.desc() if newest else Message.id.asc()
    row = (
        await session.execute(
            select(Message.id, Message.created_at)
            .where(Message.channel_id == channel_id)
            .order_by(order)
            .limit(1)
        )
    ).one_or_none()
    return Endpoint(row[0], row[1]) if row is not None else None


async def resolve_range(
    session: AsyncSession, channel_id: int, start: int | None, end: int | None
) -> ExportRange | Literal["message_not_found", "empty"]:
    """两端必须是这个频道自己的消息；开放的一端取频道最早或最新的一条（AC-3、AC-18）。"""
    start_point = await _endpoint(session, channel_id, start) if start is not None else None
    end_point = await _endpoint(session, channel_id, end) if end is not None else None
    if (start is not None and start_point is None) or (end is not None and end_point is None):
        return "message_not_found"
    if start_point is None:
        start_point = await _edge(session, channel_id, newest=False)
    if end_point is None:
        end_point = await _edge(session, channel_id, newest=True)
    if start_point is None or end_point is None:
        return "empty"
    return ExportRange(start_point, end_point)


async def count_capped(
    session: AsyncSession, channel_id: int, export_range: ExportRange, cap: int
) -> int:
    """最多数到 cap + 1 条，不扫完整个大频道（AC-6）。"""
    inner = (
        select(literal(1))
        .select_from(Message)
        .where(
            Message.channel_id == channel_id,
            Message.id >= export_range.start.id,
            Message.id <= export_range.end.id,
        )
        .limit(cap + 1)
        .subquery()
    )
    total = await session.scalar(select(func.count()).select_from(inner))
    return int(total or 0)


async def _attachments_of(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, tuple[ExportAttachment, ...]]:
    """全部附件，包括被移除的；CSV 自己跳过移除的（AC-17）。"""
    rows = await session.execute(
        select(
            Attachment.message_id,
            Attachment.id,
            Attachment.filename,
            Attachment.content_type,
            Attachment.size_bytes,
            Attachment.width,
            Attachment.height,
            Attachment.removed_at,
        )
        .where(Attachment.message_id.in_(message_ids))
        .order_by(Attachment.id)
    )
    grouped: dict[int, tuple[ExportAttachment, ...]] = {}
    for r in rows:
        item = ExportAttachment(
            r.id, r.filename, r.content_type, r.size_bytes, r.width, r.height, r.removed_at
        )
        grouped[r.message_id] = (*grouped.get(r.message_id, ()), item)
    return grouped


async def _media_of(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, tuple[ExportMedia, ...]]:
    rows = await session.execute(
        select(
            Media.message_id,
            Media.id,
            Media.kind,
            Media.attachment_id,
            Media.slot,
            Media.status,
            Media.declared_content_type,
            Media.declared_size_bytes,
        )
        .where(Media.message_id.in_(message_ids), Media.kind.in_(_EXPORT_MEDIA_KINDS))
        .order_by(Media.id)
    )
    grouped: dict[int, tuple[ExportMedia, ...]] = {}
    for r in rows:
        item = ExportMedia(
            r.id,
            r.kind,
            r.attachment_id,
            r.slot,
            r.status,
            r.declared_content_type,
            r.declared_size_bytes,
        )
        grouped[r.message_id] = (*grouped.get(r.message_id, ()), item)
    return grouped


async def _revisions_of(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, tuple[ExportRevision, ...]]:
    rows = await session.execute(
        select(
            MessageRevision.message_id,
            MessageRevision.version,
            MessageRevision.content,
            MessageRevision.embeds,
            MessageRevision.replaced_at,
        )
        .where(MessageRevision.message_id.in_(message_ids))
        .order_by(MessageRevision.message_id, MessageRevision.version)
    )
    grouped: dict[int, tuple[ExportRevision, ...]] = {}
    for r in rows:
        item = ExportRevision(r.version, r.content, tuple(r.embeds or ()), r.replaced_at)
        grouped[r.message_id] = (*grouped.get(r.message_id, ()), item)
    return grouped


async def fetch_batch(
    session: AsyncSession,
    channel_id: int,
    export_range: ExportRange,
    after: int | None,
    *,
    with_revisions: bool,
    batch_size: int = BATCH_SIZE,
) -> tuple[ExportMessage, ...]:
    """键集分页取一批，从旧到新；`after` 是上一批最后一条的 ID，第一批传 None。"""
    lower = Message.id > after if after is not None else Message.id >= export_range.start.id
    rows = (
        await session.execute(
            select(
                Message.id,
                Message.channel_id,
                Message.guild_id,
                Message.author_id,
                Message.author_display_name,
                Message.type,
                Message.content,
                Message.embeds,
                Message.forwarded,
                Message.reply_to_id,
                Message.version,
                Message.created_at,
                Message.edited_at,
                Message.deleted_at,
            )
            .where(Message.channel_id == channel_id, lower, Message.id <= export_range.end.id)
            .order_by(Message.id)
            .limit(batch_size)
        )
    ).all()
    if not rows:
        return ()
    ids = [r.id for r in rows]
    attachments = await _attachments_of(session, ids)
    media = await _media_of(session, ids)
    revisions: Mapping[int, tuple[ExportRevision, ...]] = (
        await _revisions_of(session, ids) if with_revisions else {}
    )
    return tuple(
        ExportMessage(
            id=r.id,
            channel_id=r.channel_id,
            guild_id=r.guild_id,
            author_id=r.author_id,
            author_name=r.author_display_name,
            type=r.type,
            content=r.content,
            embeds=tuple(r.embeds or ()),
            forwarded=r.forwarded,
            reply_to_id=r.reply_to_id,
            version=r.version,
            created_at=r.created_at,
            edited_at=r.edited_at,
            deleted_at=r.deleted_at,
            attachments=attachments.get(r.id, ()),
            media=media.get(r.id, ()),
            revisions=revisions.get(r.id, ()),
        )
        for r in rows
    )
