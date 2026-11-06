"""频道页的消息读取和实时通知（spec 0008）。这里的函数都不提交事务。

消息内容的唯一来源是库：接口只读这里，SSE 载荷只带 ID。
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from harvester.db.coverage_plan import BannerFacts, BannerReason, banner_reason
from harvester.db.models import (
    AccountChannelAccess,
    Attachment,
    Channel,
    ChannelCoverage,
    DiscordAccount,
    DiscordUser,
    Guild,
    Media,
    Message,
    MessageRevision,
    StoredFile,
)
from harvester.db.raw import MESSAGE_CHANGED_CHANNEL, NEW_MESSAGE_CHANNEL
from harvester.db.snapshots import (
    THREAD_CHANNEL_TYPES,
    EmbedData,
    ForwardedData,
    MediaKind,
    MediaStatus,
)

BIGINT_MAX = 2**63 - 1
REPLY_PREVIEW_LENGTH = 100

_CHANNEL_MENTION = re.compile(r"<#(\d+)>")
# @用户、@身份组、@everyone、@here 连同后面的空格（spec 0015）。
# 规则和前端 strip-at-mentions.ts 一致，改一边要同步改另一边。
_AT_MENTION = re.compile(r"(?:<@[!&]?\d+>|(?<!\w)@(?:everyone|here)\b)[ \t]*")


@dataclass(frozen=True)
class Latest:
    limit: int


@dataclass(frozen=True)
class Before:
    message_id: int
    limit: int


@dataclass(frozen=True)
class After:
    message_id: int
    limit: int


@dataclass(frozen=True)
class ByIds:
    message_ids: tuple[int, ...]


@dataclass(frozen=True)
class Around:
    """以某条为中心：前后各 AROUND_SIDE 条（搜索结果跳到上下文，spec 0013）。"""

    message_id: int


AROUND_SIDE = 25

PageSelector = Latest | Before | After | ByIds | Around


@dataclass(frozen=True)
class AttachmentRow:
    id: int
    filename: str
    content_type: str | None
    size_bytes: int


@dataclass(frozen=True)
class MediaRow:
    """一个附件或 embed 图的保存状态；已保存的用实际文件的类型和尺寸（spec 0011）。"""

    id: int
    kind: MediaKind
    attachment_id: int | None
    slot: int | None
    status: MediaStatus
    filename: str
    content_type: str | None
    size_bytes: int | None
    width: int | None
    height: int | None
    has_thumb: bool


@dataclass(frozen=True)
class ReplyMissing:
    id: int


@dataclass(frozen=True)
class ReplyFound:
    id: int
    author_name: str
    preview: str
    deleted: bool


@dataclass(frozen=True)
class MessageRow:
    id: int
    channel_id: int
    author_id: int
    author_name: str
    author_avatar_hash: str | None
    created_at: datetime
    content: str
    embeds: list[EmbedData]
    forwarded: ForwardedData | None
    attachments: tuple[AttachmentRow, ...]
    media: tuple[MediaRow, ...]
    edited_at: datetime | None
    deleted_at: datetime | None
    reply: ReplyMissing | ReplyFound | None


@dataclass(frozen=True)
class NamedRef:
    id: int
    name: str


@dataclass(frozen=True)
class MessagePage:
    messages: tuple[MessageRow, ...]
    has_more: bool
    channels: tuple[NamedRef, ...]
    # 只有 around 取法才有值：更新的消息还有没有。
    has_newer: bool | None = None


@dataclass(frozen=True)
class ChannelDetail:
    id: int
    guild_id: int
    guild_name: str
    name: str
    type: int
    banner_reason: BannerReason | None


def effective_edited_at(
    version: int, edited_at: datetime | None, last_replaced_at: datetime | None
) -> datetime | None:
    """只有真正的编辑才显示「已编辑」：embed 展开不加版本。"""
    if version <= 1:
        return None
    return edited_at or last_replaced_at


def strip_at_mentions(text: str) -> str:
    """去掉 @ 提及；改过的行去掉行尾空格，只剩提及的行整行去掉（spec 0015）。"""
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = _AT_MENTION.sub("", line)
        if stripped == line:
            lines.append(line)
        elif stripped.strip():
            lines.append(stripped.rstrip())
    return "\n".join(lines)


def reply_preview(
    content: str,
    embeds: Sequence[EmbedData] | None,
    has_attachment: bool,
    forwarded: ForwardedData | None,
) -> str:
    """回复引用的摘要；正文为空时依次回退（AC-20）。"""
    content = strip_at_mentions(content)
    title = embeds[0].get("title") if embeds else None
    if content or title:
        return (content or title or "")[:REPLY_PREVIEW_LENGTH]
    if has_attachment:
        return "[附件]"
    if forwarded is not None:
        return "[转发]"
    return "[无文字内容]"


def _message_texts(row: MessageRow) -> Iterable[str]:
    yield row.content
    embeds = row.embeds
    if row.forwarded is not None:
        yield row.forwarded["content"]
        embeds = [*embeds, *row.forwarded["embeds"]]
    for embed in embeds:
        yield embed.get("description", "")
        for field in embed.get("fields", ()):
            yield field["value"]


def _valid_ids(matches: Iterable[str]) -> frozenset[int]:
    """消息正文是第三方写的，超出 bigint 的数字不能传给数据库。"""
    return frozenset(value for value in map(int, matches) if 1 <= value <= BIGINT_MAX)


def extract_channel_mentions(texts: Iterable[str]) -> frozenset[int]:
    """从文字里取出被提及的频道 ID。@ 提及在 dashboard 里不显示（spec 0015），不用查名字。"""
    return _valid_ids(_CHANNEL_MENTION.findall("\n".join(texts)))


def _message_select():
    """频道页和搜索共用的消息列；作者头像来自 discord_user。"""
    author = aliased(DiscordUser)
    return select(
        Message.id,
        Message.channel_id,
        Message.author_id,
        Message.author_display_name,
        author.avatar_hash,
        Message.created_at,
        Message.content,
        Message.embeds,
        Message.forwarded,
        Message.reply_to_id,
        Message.version,
        Message.edited_at,
        Message.deleted_at,
    ).join(author, author.id == Message.author_id)


def messages_by_ids_statement(message_ids: Sequence[int]):
    return _message_select().where(Message.id.in_(message_ids))


def _page_statement(channel_id: int, selector: PageSelector):
    stmt = _message_select().where(Message.channel_id == channel_id)
    match selector:
        case Latest(limit=limit):
            return stmt.order_by(Message.id.desc()).limit(limit + 1)
        case Before(message_id=message_id, limit=limit):
            return stmt.where(Message.id < message_id).order_by(Message.id.desc()).limit(limit + 1)
        case After(message_id=message_id, limit=limit):
            return stmt.where(Message.id > message_id).order_by(Message.id.asc()).limit(limit + 1)
        case ByIds(message_ids=message_ids):
            return stmt.where(Message.id.in_(message_ids)).order_by(Message.id.asc())
        case Around(message_id=message_id):
            return stmt.where(Message.id == message_id)


def _limit_of(selector: PageSelector) -> int | None:
    return None if isinstance(selector, ByIds | Around) else selector.limit


async def _attachments_by_message(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, tuple[AttachmentRow, ...]]:
    rows = await session.execute(
        select(
            Attachment.message_id,
            Attachment.id,
            Attachment.filename,
            Attachment.content_type,
            Attachment.size_bytes,
        )
        .where(Attachment.message_id.in_(message_ids), Attachment.removed_at.is_(None))
        .order_by(Attachment.id)
    )
    grouped: dict[int, tuple[AttachmentRow, ...]] = {}
    for r in rows:
        row = AttachmentRow(r.id, r.filename, r.content_type, r.size_bytes)
        grouped[r.message_id] = (*grouped.get(r.message_id, ()), row)
    return grouped


async def media_by_message(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, tuple[MediaRow, ...]]:
    """当前页的 media 一次查出（走 ix_media_message），不逐条查。"""
    rows = await session.execute(
        select(
            Media.message_id,
            Media.id,
            Media.kind,
            Media.attachment_id,
            Media.slot,
            Media.status,
            Media.filename,
            Media.declared_content_type,
            Media.declared_size_bytes,
            Media.declared_width,
            Media.declared_height,
            StoredFile.content_type.label("stored_content_type"),
            StoredFile.size_bytes.label("stored_size_bytes"),
            StoredFile.width.label("stored_width"),
            StoredFile.height.label("stored_height"),
            StoredFile.thumb_object_key,
        )
        .outerjoin(StoredFile, StoredFile.id == Media.file_id)
        .where(Media.message_id.in_(message_ids))
        .order_by(Media.kind, Media.attachment_id, Media.slot)
    )
    grouped: dict[int, tuple[MediaRow, ...]] = {}
    for r in rows:
        stored = r.status == "stored"
        row = MediaRow(
            id=r.id,
            kind=r.kind,
            attachment_id=r.attachment_id,
            slot=r.slot,
            status=r.status,
            filename=r.filename,
            content_type=r.stored_content_type if stored else r.declared_content_type,
            size_bytes=r.stored_size_bytes if stored else r.declared_size_bytes,
            width=(r.stored_width or r.declared_width) if stored else r.declared_width,
            height=(r.stored_height or r.declared_height) if stored else r.declared_height,
            has_thumb=r.thumb_object_key is not None,
        )
        grouped[r.message_id] = (*grouped.get(r.message_id, ()), row)
    return grouped


async def _last_replaced_at(
    session: AsyncSession, message_ids: Sequence[int]
) -> Mapping[int, datetime]:
    if not message_ids:
        return {}
    rows = await session.execute(
        select(MessageRevision.message_id, func.max(MessageRevision.replaced_at))
        .where(MessageRevision.message_id.in_(message_ids))
        .group_by(MessageRevision.message_id)
    )
    return {message_id: replaced_at for message_id, replaced_at in rows}


async def _replies_by_id(
    session: AsyncSession, reply_ids: frozenset[int]
) -> Mapping[int, ReplyFound]:
    """被回复的消息按主键查，不限频道。"""
    if not reply_ids:
        return {}
    has_attachment = exists(
        select(1).where(Attachment.message_id == Message.id, Attachment.removed_at.is_(None))
    ).label("has_attachment")
    rows = await session.execute(
        select(
            Message.id,
            Message.author_display_name,
            Message.content,
            Message.embeds,
            Message.forwarded,
            Message.deleted_at,
            has_attachment,
        ).where(Message.id.in_(reply_ids))
    )
    return {
        r.id: ReplyFound(
            id=r.id,
            author_name=r.author_display_name,
            preview=reply_preview(r.content, r.embeds, r.has_attachment, r.forwarded),
            deleted=r.deleted_at is not None,
        )
        for r in rows
    }


async def resolve_channel_names(
    session: AsyncSession, messages: Sequence[MessageRow]
) -> tuple[NamedRef, ...]:
    channel_ids = extract_channel_mentions(t for m in messages for t in _message_texts(m))
    if not channel_ids:
        return ()
    rows = await session.execute(
        select(Channel.id, Channel.name).where(Channel.id.in_(channel_ids))
    )
    return tuple(NamedRef(r.id, r.name) for r in rows)


async def hydrate_messages(session: AsyncSession, ordered: Sequence[Any]) -> tuple[MessageRow, ...]:
    """把 `_message_select` 查出的行补上附件、媒体、回复摘要，保持传入的顺序。"""
    ids = [r.id for r in ordered]
    attachments = await _attachments_by_message(session, ids)
    media = await media_by_message(session, ids)
    replaced = await _last_replaced_at(
        session, [r.id for r in ordered if r.version > 1 and r.edited_at is None]
    )
    replies = await _replies_by_id(
        session, frozenset(r.reply_to_id for r in ordered if r.reply_to_id is not None)
    )

    def reply_of(reply_id: int | None) -> ReplyFound | ReplyMissing | None:
        if reply_id is None:
            return None
        return replies.get(reply_id, ReplyMissing(reply_id))

    return tuple(
        MessageRow(
            id=r.id,
            channel_id=r.channel_id,
            author_id=r.author_id,
            author_name=r.author_display_name,
            author_avatar_hash=r.avatar_hash,
            created_at=r.created_at,
            content=r.content,
            embeds=r.embeds or [],
            forwarded=r.forwarded,
            attachments=attachments.get(r.id, ()),
            media=media.get(r.id, ()),
            edited_at=effective_edited_at(r.version, r.edited_at, replaced.get(r.id)),
            deleted_at=r.deleted_at,
            reply=reply_of(r.reply_to_id),
        )
        for r in ordered
    )


async def get_message_page(
    session: AsyncSession, channel_id: int, selector: PageSelector
) -> MessagePage | Literal["channel_not_found", "message_not_found"]:
    """按 ID 从旧到新返回一页消息，已删除的也在内。"""
    if await session.scalar(select(Channel.id).where(Channel.id == channel_id)) is None:
        return "channel_not_found"
    if isinstance(selector, Around):
        return await _get_around_page(session, channel_id, selector.message_id)
    rows = list(await session.execute(_page_statement(channel_id, selector)))
    limit = _limit_of(selector)
    has_more = limit is not None and len(rows) > limit
    kept = rows[:limit] if limit is not None else rows
    # 多查的一行用来判断 has_more；往旧的方向查是倒序的，翻回从旧到新。
    ordered = kept if isinstance(selector, After | ByIds) else list(reversed(kept))

    messages = await hydrate_messages(session, ordered)
    channels = await resolve_channel_names(session, messages)
    return MessagePage(messages, has_more, channels)


async def _get_around_page(
    session: AsyncSession, channel_id: int, message_id: int
) -> MessagePage | Literal["message_not_found"]:
    """目标之前 25 条、目标本身、之后 25 条；两边各多取一条判断还有没有。"""
    target = list(await session.execute(_page_statement(channel_id, Around(message_id))))
    if not target:
        return "message_not_found"
    older = list(
        await session.execute(_page_statement(channel_id, Before(message_id, AROUND_SIDE)))
    )
    newer = list(await session.execute(_page_statement(channel_id, After(message_id, AROUND_SIDE))))
    ordered = [*reversed(older[:AROUND_SIDE]), *target, *newer[:AROUND_SIDE]]
    messages = await hydrate_messages(session, ordered)
    channels = await resolve_channel_names(session, messages)
    return MessagePage(
        messages,
        has_more=len(older) > AROUND_SIDE,
        channels=channels,
        has_newer=len(newer) > AROUND_SIDE,
    )


async def get_channel_detail(session: AsyncSession, channel_id: int) -> ChannelDetail | None:
    """频道页顶部要的信息；`banner_reason` 由后端算好，前端不用拼条件。"""
    row = (
        await session.execute(
            select(
                Channel.id,
                Channel.guild_id,
                Channel.name,
                Channel.type,
                Channel.status,
                Channel.parent_id,
                Channel.collect_enabled,
                Channel.collector_account_id,
                Guild.name.label("guild_name"),
                Guild.status.label("guild_status"),
            )
            .join(Guild, Guild.id == Channel.guild_id)
            .where(Channel.id == channel_id)
        )
    ).one_or_none()
    if row is None:
        return None
    account = (
        await session.execute(select(DiscordAccount.id, DiscordAccount.status))
    ).one_or_none()
    account_id = account.id if account is not None else None

    async def can_read(target_id: int) -> bool:
        return bool(
            await session.scalar(
                select(AccountChannelAccess.can_read).where(
                    AccountChannelAccess.channel_id == target_id,
                    AccountChannelAccess.account_id == account_id,
                )
            )
        )

    parent_status = (
        await session.scalar(select(Channel.status).where(Channel.id == row.parent_id))
        if row.parent_id is not None
        else None
    )
    segment_open = await session.scalar(
        select(
            exists().where(
                ChannelCoverage.channel_id == channel_id,
                text("channel_coverage.source = 'realtime' AND upper_inf(channel_coverage.span)"),
            )
        )
    )
    facts = BannerFacts(
        channel_status=row.status,
        guild_status=row.guild_status,
        collecting=(
            row.collect_enabled
            and account_id is not None
            and row.collector_account_id == account_id
        ),
        can_read=await can_read(channel_id),
        is_thread=row.type in THREAD_CHANNEL_TYPES,
        parent_active=parent_status == "active",
        parent_can_read=row.parent_id is not None and await can_read(row.parent_id),
        account_status=account.status if account is not None else None,
        segment_open=bool(segment_open),
    )
    return ChannelDetail(
        id=row.id,
        guild_id=row.guild_id,
        guild_name=row.guild_name,
        name=row.name,
        type=row.type,
        banner_reason=banner_reason(facts),
    )


async def _notify(session: AsyncSession, channel: str, payload: Mapping[str, object]) -> None:
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": channel, "payload": json.dumps(payload)},
    )


async def notify_new_message(session: AsyncSession, channel_id: int, message_id: int) -> None:
    # ID 必须是字符串，否则浏览器的 JSON.parse 会丢精度。
    await _notify(
        session,
        NEW_MESSAGE_CHANNEL,
        {"channel_id": str(channel_id), "message_id": str(message_id)},
    )


async def notify_message_changed(
    session: AsyncSession, channel_id: int, message_ids: Sequence[int]
) -> None:
    await _notify(
        session,
        MESSAGE_CHANGED_CHANNEL,
        {"channel_id": str(channel_id), "message_ids": [str(i) for i in message_ids]},
    )
