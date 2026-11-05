"""搜索接口（spec 0013）：只读库。日志不记关键词和消息内容（AC-25）。"""

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from harvester.api.errors import ApiError
from harvester.api.message_routes import (
    MessageOut,
    NamedRefOut,
    message_out,
    named_out,
)
from harvester.api.search_query import (
    FieldError,
    FilterError,
    fields_payload,
    parse_filters,
    parse_ids,
)
from harvester.db import SessionFactoryDep
from harvester.db import search as search_store
from harvester.db.models import Message
from harvester.db.search import PAGE_SIZE, SearchFilters

search_router = APIRouter(prefix="/search", tags=["search"])

log = structlog.get_logger("harvester.search")


class InvalidSearchError(ApiError):
    def __init__(self, error: FilterError) -> None:
        message = "关键词不正确" if error.code == "invalid_query" else "筛选条件不正确"
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            error.code,
            message,
            fields=fields_payload(error.fields),
        )


class SearchTimeoutError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "search_timeout", "搜索超时")


class SearchGuildOut(BaseModel):
    id: str
    name: str


class SearchParentOut(BaseModel):
    id: str
    name: str


class SearchChannelOut(BaseModel):
    id: str
    name: str
    type: int
    status: str
    collect_enabled: bool
    parent: SearchParentOut | None


class SearchHitOut(BaseModel):
    message: MessageOut
    guild: SearchGuildOut
    channel: SearchChannelOut


class SearchMessagesOut(BaseModel):
    items: list[SearchHitOut]
    next_before: str | None
    users: list[NamedRefOut]
    channels: list[NamedRefOut]


class SearchCountOut(BaseModel):
    count: int | None
    capped: bool


class SearchStatusOut(BaseModel):
    index_pending: int


# 所有参数按原始字符串收进来，交给 parse_filters；不用 pydantic 校验，
# 否则出错时返回的是 validation_error 而不是 invalid_filters。
Raw = Annotated[str | None, Query()]


def _filters(
    q: str | None,
    guild: str | None,
    channel: str | None,
    author: str | None,
    time_from: str | None,
    time_to: str | None,
) -> SearchFilters:
    parsed = parse_filters(q, guild, channel, author, time_from, time_to)
    if isinstance(parsed, FilterError):
        raise InvalidSearchError(parsed)
    return parsed


def _before(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 19 and 1 <= int(raw) <= 2**63 - 1):
        raise InvalidSearchError(FilterError("invalid_filters", (_field("before", "invalid_id"),)))
    return int(raw)


def _field(name: str, kind: str) -> FieldError:
    return FieldError(("query", name), kind)


@search_router.get("/messages")
async def search_messages(
    factory: SessionFactoryDep,
    q: Raw = None,
    guild: Raw = None,
    channel: Raw = None,
    author: Raw = None,
    time_from: Annotated[str | None, Query(alias="from")] = None,
    time_to: Annotated[str | None, Query(alias="to")] = None,
    before: Raw = None,
    limit: Annotated[int, Query(ge=1, le=PAGE_SIZE)] = PAGE_SIZE,
) -> SearchMessagesOut:
    filters = _filters(q, guild, channel, author, time_from, time_to)
    cursor = _before(before)
    started = time.monotonic()
    page = await search_store.search_page(factory, filters, cursor, limit)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if page == "timeout":
        log.warning(
            "search.timeout", terms=len(filters.include) + len(filters.exclude), ms=elapsed_ms
        )
        raise SearchTimeoutError
    log.info(
        "search.done",
        terms=len(filters.include) + len(filters.exclude),
        has_guild=bool(filters.guilds),
        has_channel=bool(filters.channels),
        has_author=bool(filters.authors),
        has_time=filters.time_from is not None or filters.time_to is not None,
        results=len(page.hits),
        ms=elapsed_ms,
    )
    return SearchMessagesOut(
        items=[
            SearchHitOut(
                message=message_out(hit.message),
                guild=SearchGuildOut(id=str(hit.guild.id), name=hit.guild.name),
                channel=SearchChannelOut(
                    id=str(hit.channel.id),
                    name=hit.channel.name,
                    type=hit.channel.type,
                    status=hit.channel.status,
                    collect_enabled=hit.channel.collect_enabled,
                    parent=(
                        SearchParentOut(id=str(hit.channel.parent.id), name=hit.channel.parent.name)
                        if hit.channel.parent
                        else None
                    ),
                ),
            )
            for hit in page.hits
        ],
        next_before=str(page.next_before) if page.next_before is not None else None,
        users=named_out(page.users),
        channels=named_out(page.channels),
    )


@search_router.get("/count")
async def search_count(
    factory: SessionFactoryDep,
    q: Raw = None,
    guild: Raw = None,
    channel: Raw = None,
    author: Raw = None,
    time_from: Annotated[str | None, Query(alias="from")] = None,
    time_to: Annotated[str | None, Query(alias="to")] = None,
) -> SearchCountOut:
    filters = _filters(q, guild, channel, author, time_from, time_to)
    result = await search_store.count_matches(factory, filters)
    if result is None:
        return SearchCountOut(count=None, capped=False)
    return SearchCountOut(count=result.count, capped=result.capped)


@search_router.get("/status")
async def search_status(factory: SessionFactoryDep) -> SearchStatusOut:
    async with factory() as session:
        pending = await session.scalar(
            select(func.count()).select_from(Message).where(Message.search_text.is_(None))
        )
    return SearchStatusOut(index_pending=int(pending or 0))


class PickerChannelOut(BaseModel):
    id: str
    name: str
    type: int
    parent_id: str | None
    category_name: str | None
    status: str
    collect_enabled: bool


class PickerGuildOut(BaseModel):
    id: str
    name: str
    status: str
    channels: list[PickerChannelOut]


class PickerOut(BaseModel):
    guilds: list[PickerGuildOut]


class AuthorOut(BaseModel):
    id: str
    username: str
    global_name: str | None
    avatar_hash: str | None


class AuthorsOut(BaseModel):
    authors: list[AuthorOut]


@search_router.get("/channels")
async def search_channels(factory: SessionFactoryDep) -> PickerOut:
    async with factory() as session:
        guilds = await search_store.list_picker(session)
    return PickerOut(
        guilds=[
            PickerGuildOut(
                id=str(g.id),
                name=g.name,
                status=g.status,
                channels=[
                    PickerChannelOut(
                        id=str(c.id),
                        name=c.name,
                        type=c.type,
                        parent_id=str(c.parent_id) if c.parent_id is not None else None,
                        category_name=c.category_name,
                        status=c.status,
                        collect_enabled=c.collect_enabled,
                    )
                    for c in g.channels
                ],
            )
            for g in guilds
        ]
    )


@search_router.get("/authors")
async def search_authors(factory: SessionFactoryDep, q: Raw = None, ids: Raw = None) -> AuthorsOut:
    """`q`（1 到 50 字符）和 `ids`（最多 20 个）二选一。"""
    if (q is None) == (ids is None):
        raise InvalidSearchError(FilterError("invalid_filters", (_field("q", "no_conditions"),)))
    async with factory() as session:
        if ids is not None:
            parsed, error = parse_ids(ids, "ids", 20)
            if error is not None:
                raise InvalidSearchError(FilterError("invalid_filters", (error,)))
            rows = await search_store.authors_by_ids(session, parsed)
        else:
            assert q is not None
            if not 1 <= len(q) <= 50:
                raise InvalidSearchError(FilterError("invalid_filters", (_field("q", "invalid"),)))
            rows = await search_store.suggest_authors(session, q)
    return AuthorsOut(
        authors=[
            AuthorOut(
                id=str(r.id),
                username=r.username,
                global_name=r.global_name,
                avatar_hash=r.avatar_hash,
            )
            for r in rows
        ]
    )
