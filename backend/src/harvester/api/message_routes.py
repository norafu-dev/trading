"""频道页接口（spec 0008）：只读库，不调用 Discord。"""

from typing import Annotated

from fastapi import APIRouter, Path, Query, status
from pydantic import AfterValidator, BaseModel, StringConstraints

from harvester.api.deps import ClockDep
from harvester.api.errors import ApiError
from harvester.db import SessionFactoryDep
from harvester.db import channels as channel_store
from harvester.db import messages as message_store
from harvester.db import read_state as read_store
from harvester.db.channels import ThreadLoadError
from harvester.db.coverage_plan import BannerReason
from harvester.db.messages import (
    BIGINT_MAX,
    After,
    AttachmentRow,
    Before,
    ByIds,
    Latest,
    MediaRow,
    MessageRow,
    NamedRef,
    PageSelector,
    ReplyMissing,
)
from harvester.db.snapshots import EmbedData, MediaKind, MediaStatus

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承。
message_router = APIRouter(prefix="/discord", tags=["messages"])

MAX_IDS = 100
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _check_id(raw: str) -> str:
    if not 1 <= int(raw) <= BIGINT_MAX:
        raise ValueError("id_out_of_range")
    return raw


def _check_ids(raw: str) -> str:
    """逗号分隔的 ID：不能有空项，去重后最多 100 个。"""
    parts = raw.split(",")
    for part in parts:
        if not part.isascii() or not part.isdigit():
            raise ValueError("id_invalid")
        _check_id(part)
    if len(set(parts)) > MAX_IDS:
        raise ValueError("too_many_ids")
    return raw


# snowflake 超过 JS 的安全整数，接口上一律是字符串。
PathId = Annotated[str, Path(pattern=r"^[0-9]{1,19}$"), AfterValidator(_check_id)]
QueryId = Annotated[str, Query(pattern=r"^[0-9]{1,19}$"), AfterValidator(_check_id)]
BodyId = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,19}$"), AfterValidator(_check_id)]
QueryIds = Annotated[str, Query(max_length=8000), AfterValidator(_check_ids)]


class ChannelNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "channel_not_found", "频道不存在")


class ConflictingCursorsError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "before、after、ids 只能用一个",
        )


_THREAD_LOAD_ERRORS: dict[ThreadLoadError, tuple[int, str]] = {
    "guild_not_found": (status.HTTP_404_NOT_FOUND, "服务器不存在"),
    "account_not_found": (status.HTTP_409_CONFLICT, "还没有添加账号"),
    "guild_unavailable": (status.HTTP_409_CONFLICT, "小号已退出这个服务器，或者服务器已删除"),
}


class ThreadLoadFailedError(ApiError):
    def __init__(self, code: ThreadLoadError) -> None:
        http_status, message = _THREAD_LOAD_ERRORS[code]
        super().__init__(http_status, code, message)


class AttachmentOut(BaseModel):
    id: str
    filename: str
    content_type: str | None
    size_bytes: int


class MediaOut(BaseModel):
    id: str
    kind: MediaKind
    attachment_id: str | None
    slot: int | None
    status: MediaStatus
    filename: str
    content_type: str | None
    size_bytes: int | None
    width: int | None
    height: int | None
    has_thumb: bool


class ForwardedOut(BaseModel):
    message_id: str
    channel_id: str
    guild_id: str | None
    content: str
    embeds: list[EmbedData]
    attachment_names: list[str]


class ReplyOut(BaseModel):
    id: str
    missing: bool
    # 下面三个只在 missing 为假时有值。
    author_name: str | None = None
    preview: str | None = None
    deleted: bool | None = None


class MessageOut(BaseModel):
    id: str
    channel_id: str
    author_id: str
    author_name: str
    author_avatar_hash: str | None
    created_at: str
    content: str
    embeds: list[EmbedData]
    forwarded: ForwardedOut | None
    attachments: list[AttachmentOut]
    media: list[MediaOut]
    edited_at: str | None
    deleted_at: str | None
    reply: ReplyOut | None


class NamedRefOut(BaseModel):
    id: str
    name: str


class MessagesOut(BaseModel):
    messages: list[MessageOut]
    has_more: bool
    users: list[NamedRefOut]
    channels: list[NamedRefOut]


class ChannelDetailOut(BaseModel):
    id: str
    guild_id: str
    guild_name: str
    name: str
    type: int
    banner_reason: BannerReason | None


class ThreadLoadOut(BaseModel):
    pending: bool


class UnreadChannelOut(BaseModel):
    channel_id: str
    count: int


class UnreadOut(BaseModel):
    channels: list[UnreadChannelOut]


class MarkReadIn(BaseModel):
    message_id: BodyId


class MarkReadOut(BaseModel):
    last_read_message_id: str


def _attachment_out(row: AttachmentRow) -> AttachmentOut:
    return AttachmentOut(
        id=str(row.id),
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
    )


def media_out(row: MediaRow) -> MediaOut:
    return MediaOut(
        id=str(row.id),
        kind=row.kind,
        attachment_id=str(row.attachment_id) if row.attachment_id is not None else None,
        slot=row.slot,
        status=row.status,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        width=row.width,
        height=row.height,
        has_thumb=row.has_thumb,
    )


def _reply_out(row: MessageRow) -> ReplyOut | None:
    reply = row.reply
    if reply is None:
        return None
    if isinstance(reply, ReplyMissing):
        return ReplyOut(id=str(reply.id), missing=True)
    return ReplyOut(
        id=str(reply.id),
        missing=False,
        author_name=reply.author_name,
        preview=reply.preview,
        deleted=reply.deleted,
    )


def _forwarded_out(row: MessageRow) -> ForwardedOut | None:
    forwarded = row.forwarded
    if forwarded is None:
        return None
    guild_id = forwarded["guild_id"]
    return ForwardedOut(
        message_id=str(forwarded["message_id"]),
        channel_id=str(forwarded["channel_id"]),
        guild_id=str(guild_id) if guild_id is not None else None,
        content=forwarded["content"],
        embeds=forwarded["embeds"],
        attachment_names=forwarded["attachment_names"],
    )


def _message_out(row: MessageRow) -> MessageOut:
    return MessageOut(
        id=str(row.id),
        channel_id=str(row.channel_id),
        author_id=str(row.author_id),
        author_name=row.author_name,
        author_avatar_hash=row.author_avatar_hash,
        created_at=row.created_at.isoformat(),
        content=row.content,
        embeds=row.embeds,
        forwarded=_forwarded_out(row),
        attachments=[_attachment_out(a) for a in row.attachments],
        media=[media_out(m) for m in row.media],
        edited_at=row.edited_at.isoformat() if row.edited_at else None,
        deleted_at=row.deleted_at.isoformat() if row.deleted_at else None,
        reply=_reply_out(row),
    )


def _named(rows: tuple[NamedRef, ...]) -> list[NamedRefOut]:
    return [NamedRefOut(id=str(r.id), name=r.name) for r in rows]


def _selector(before: str | None, after: str | None, ids: str | None, limit: int) -> PageSelector:
    if sum(value is not None for value in (before, after, ids)) > 1:
        raise ConflictingCursorsError
    if before is not None:
        return Before(int(before), limit)
    if after is not None:
        return After(int(after), limit)
    if ids is not None:
        return ByIds(tuple(dict.fromkeys(int(part) for part in ids.split(","))))
    return Latest(limit)


@message_router.get("/channels/{channel_id}")
async def get_channel(channel_id: PathId, factory: SessionFactoryDep) -> ChannelDetailOut:
    async with factory() as session:
        detail = await message_store.get_channel_detail(session, int(channel_id))
    if detail is None:
        raise ChannelNotFoundError
    return ChannelDetailOut(
        id=str(detail.id),
        guild_id=str(detail.guild_id),
        guild_name=detail.guild_name,
        name=detail.name,
        type=detail.type,
        banner_reason=detail.banner_reason,
    )


@message_router.get("/channels/{channel_id}/messages")
async def list_messages(
    channel_id: PathId,
    factory: SessionFactoryDep,
    before: QueryId | None = None,
    after: QueryId | None = None,
    ids: QueryIds | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> MessagesOut:
    selector = _selector(before, after, ids, limit)
    async with factory() as session:
        page = await message_store.get_message_page(session, int(channel_id), selector)
    if page == "channel_not_found":
        raise ChannelNotFoundError
    return MessagesOut(
        messages=[_message_out(m) for m in page.messages],
        has_more=page.has_more,
        users=_named(page.users),
        channels=_named(page.channels),
    )


@message_router.get("/unread")
async def list_unread(factory: SessionFactoryDep) -> UnreadOut:
    async with factory() as session:
        rows = await read_store.list_unread(session)
    return UnreadOut(
        channels=[UnreadChannelOut(channel_id=str(r.channel_id), count=r.count) for r in rows]
    )


@message_router.post("/channels/{channel_id}/read")
async def mark_read(
    channel_id: PathId, body: MarkReadIn, factory: SessionFactoryDep
) -> MarkReadOut:
    async with factory() as session, session.begin():
        position = await read_store.mark_read(session, int(channel_id), int(body.message_id))
    if position == "channel_not_found":
        raise ChannelNotFoundError
    return MarkReadOut(last_read_message_id=str(position))


@message_router.post("/guilds/{guild_id}/load-threads", status_code=status.HTTP_202_ACCEPTED)
async def load_threads(
    guild_id: PathId, factory: SessionFactoryDep, clock: ClockDep
) -> ThreadLoadOut:
    async with factory() as session, session.begin():
        error = await channel_store.request_thread_load(session, int(guild_id), clock())
    if error is not None:
        raise ThreadLoadFailedError(error)
    return ThreadLoadOut(pending=True)
