"""消息搜索（spec 0013）：搜索文字的写入、查询条件构造、搜索和总数。

函数都不提交事务。查询条件 `search_conditions` 搜索、总数和以后的导出共用。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import structlog
from sqlalchemy import ColumnElement, and_, exists, func, literal, not_, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.sqltypes import Text

from harvester.db.messages import (
    MessageRow,
    NamedRef,
    hydrate_messages,
    messages_by_ids_statement,
    resolve_channel_names,
)
from harvester.db.models import Channel, DiscordUser, Guild, Message
from harvester.db.snapshots import THREAD_CHANNEL_TYPES, snowflake_floor

log = structlog.get_logger("harvester.search")

SEARCH_TIMEOUT_MS = 10_000
COUNT_TIMEOUT_MS = 5_000
COUNT_CAP = 1000
PAGE_SIZE = 50
_QUERY_CANCELED = "57014"


@dataclass(frozen=True)
class SearchFilters:
    """已经解析、校验过的搜索条件；词已经小写、合并空白。"""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    guilds: tuple[int, ...] = ()
    channels: tuple[int, ...] = ()
    authors: tuple[int, ...] = ()
    time_from: datetime | None = None
    time_to: datetime | None = None


@dataclass(frozen=True)
class ChannelInfo:
    id: int
    name: str
    type: int
    status: str
    collect_enabled: bool
    parent: NamedRef | None


@dataclass(frozen=True)
class SearchHit:
    message: MessageRow
    guild: NamedRef
    channel: ChannelInfo


@dataclass(frozen=True)
class SearchPage:
    hits: tuple[SearchHit, ...]
    next_before: int | None
    channels: tuple[NamedRef, ...]


@dataclass(frozen=True)
class SearchCount:
    count: int
    capped: bool


# --- 查询条件 --------------------------------------------------------------


def escape_like(term: str) -> str:
    """`%`、`_`、`\\` 当普通字符。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bigrams(value: Any) -> ColumnElement[Any]:
    return func.harvester_bigrams(value, type_=ARRAY(Text))


def _contains(term: str) -> ColumnElement[bool]:
    return Message.search_text.like(f"%{escape_like(term)}%", escape="\\")


def search_conditions(filters: SearchFilters) -> list[ColumnElement[bool]]:
    """全部条件之间是「并且」（AC-11）。含包含词时，二元切片的包含条件让 GIN 索引先缩小范围。"""
    conditions: list[ColumnElement[bool]] = []
    if filters.include:
        needed = _bigrams(literal(filters.include[0]))
        for term in filters.include[1:]:
            needed = needed.op("||", return_type=ARRAY(Text))(_bigrams(literal(term)))
        conditions.append(_bigrams(Message.search_text).op("@>")(needed))
        conditions.extend(_contains(term) for term in filters.include)
    # 还没补建（NULL）的行，NOT LIKE 的结果是 NULL，自然被排除（AC-22）。
    conditions.extend(not_(_contains(term)) for term in filters.exclude)

    place: list[ColumnElement[bool]] = []
    if filters.guilds:
        place.append(Message.guild_id.in_(filters.guilds))
    if filters.channels:
        place.append(Message.channel_id.in_(filters.channels))
        # parent_id 对普通频道是分类，对线程才是父频道，所以必须带类型条件。
        threads = select(Channel.id).where(
            Channel.parent_id.in_(filters.channels), Channel.type.in_(THREAD_CHANNEL_TYPES)
        )
        place.append(Message.channel_id.in_(threads))
    if place:
        conditions.append(or_(*place))
    if filters.authors:
        conditions.append(Message.author_id.in_(filters.authors))
    if filters.time_from is not None:
        conditions.append(Message.id >= snowflake_floor(filters.time_from))
    if filters.time_to is not None:
        conditions.append(Message.id < snowflake_floor(filters.time_to))
    return conditions


def search_ids_statement(filters: SearchFilters, before: int | None, limit: int):
    conditions = search_conditions(filters)
    if before is not None:
        conditions.append(Message.id < before)
    return select(Message.id).where(and_(*conditions)).order_by(Message.id.desc()).limit(limit + 1)


async def _apply_plan_settings(
    session: AsyncSession, filters: SearchFilters, timeout_ms: int
) -> None:
    """超时和固定计划，只对当前事务有效（SET LOCAL）。"""
    await session.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
    if filters.include:
        await session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
        await session.execute(text("SET LOCAL enable_indexscan = off"))
        await session.execute(text("SET LOCAL enable_seqscan = off"))


def is_query_canceled(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) == _QUERY_CANCELED


# --- 搜索和总数 ------------------------------------------------------------


def _channel_info_statement(channel_ids: Sequence[int]):
    parent = Channel.__table__.alias("parent")
    return (
        select(
            Channel.id,
            Channel.name,
            Channel.type,
            Channel.status,
            Channel.collect_enabled,
            Channel.guild_id,
            Guild.name.label("guild_name"),
            parent.c.id.label("parent_id"),
            parent.c.name.label("parent_name"),
        )
        .join(Guild, Guild.id == Channel.guild_id)
        .outerjoin(
            parent,
            and_(parent.c.id == Channel.parent_id, Channel.type.in_(THREAD_CHANNEL_TYPES)),
        )
        .where(Channel.id.in_(channel_ids))
    )


def _channel_info(row: Any) -> ChannelInfo:
    parent = NamedRef(row.parent_id, row.parent_name) if row.parent_id is not None else None
    return ChannelInfo(
        id=row.id,
        name=row.name,
        type=row.type,
        status=row.status,
        collect_enabled=row.collect_enabled,
        parent=parent,
    )


async def search_page(
    factory: async_sessionmaker[AsyncSession],
    filters: SearchFilters,
    before: int | None,
    limit: int,
) -> SearchPage | Literal["timeout"]:
    """一页搜索结果，最新的在前；多取一条判断有没有下一页。"""
    try:
        async with factory() as session, session.begin():
            await _apply_plan_settings(session, filters, SEARCH_TIMEOUT_MS)
            ids = list(await session.scalars(search_ids_statement(filters, before, limit)))
            has_more = len(ids) > limit
            ids = ids[:limit]
            if not ids:
                return SearchPage((), None, ())
            rows = list(await session.execute(messages_by_ids_statement(ids)))
            rows.sort(key=lambda r: r.id, reverse=True)
            messages = await hydrate_messages(session, rows)
            channel_ids = sorted({m.channel_id for m in messages})
            infos = {
                r.id: (NamedRef(r.guild_id, r.guild_name), _channel_info(r))
                for r in await session.execute(_channel_info_statement(channel_ids))
            }
            channels = await resolve_channel_names(session, messages)
    except DBAPIError as error:
        if is_query_canceled(error):
            return "timeout"
        raise
    hits = tuple(
        SearchHit(message=m, guild=infos[m.channel_id][0], channel=infos[m.channel_id][1])
        for m in messages
    )
    return SearchPage(hits, messages[-1].id if has_more else None, channels)


async def count_matches(
    factory: async_sessionmaker[AsyncSession], filters: SearchFilters
) -> SearchCount | None:
    """最多数到 1000；超时返回 None（AC-14）。"""
    inner = select(literal(1)).select_from(Message).where(and_(*search_conditions(filters)))
    try:
        async with factory() as session, session.begin():
            await _apply_plan_settings(session, filters, COUNT_TIMEOUT_MS)
            total = await session.scalar(
                select(func.count()).select_from(inner.limit(COUNT_CAP + 1).subquery())
            )
    except DBAPIError as error:
        if is_query_canceled(error):
            return None
        raise
    total = int(total or 0)
    return SearchCount(min(total, COUNT_CAP), total > COUNT_CAP)


# --- 频道选择器和作者联想 ---------------------------------------------------


@dataclass(frozen=True)
class PickerChannel:
    id: int
    name: str
    type: int
    parent_id: int | None
    category_name: str | None
    status: str
    collect_enabled: bool


@dataclass(frozen=True)
class PickerGuild:
    id: int
    name: str
    status: str
    channels: tuple[PickerChannel, ...]


@dataclass(frozen=True)
class AuthorRow:
    id: int
    username: str
    global_name: str | None
    avatar_hash: str | None


MAX_AUTHOR_SUGGESTIONS = 20
_CATEGORY_TYPE = 4


async def list_picker(session: AsyncSession) -> tuple[PickerGuild, ...]:
    """只列至少有一条消息的服务器和频道；有消息的线程让它的父频道也列出（AC-8）。"""
    child = Channel.__table__.alias("child")
    own = exists().where(Message.channel_id == Channel.id)
    via_thread = exists().where(
        child.c.parent_id == Channel.id,
        child.c.type.in_(THREAD_CHANNEL_TYPES),
        exists().where(Message.channel_id == child.c.id),
    )
    category = Channel.__table__.alias("category")
    rows = (
        await session.execute(
            select(
                Channel.id,
                Channel.guild_id,
                Channel.name,
                Channel.type,
                Channel.parent_id,
                Channel.position,
                Channel.status,
                Channel.collect_enabled,
                category.c.name.label("category_name"),
                category.c.position.label("category_position"),
                Guild.name.label("guild_name"),
                Guild.status.label("guild_status"),
            )
            .join(Guild, Guild.id == Channel.guild_id)
            .outerjoin(
                category,
                and_(category.c.id == Channel.parent_id, category.c.type == _CATEGORY_TYPE),
            )
            .where(Channel.type != _CATEGORY_TYPE, or_(own, via_thread))
        )
    ).all()

    guilds: dict[int, list[Any]] = {}
    for row in rows:
        guilds.setdefault(row.guild_id, []).append(row)

    def picker(row: Any) -> PickerChannel:
        is_thread = row.type in THREAD_CHANNEL_TYPES
        return PickerChannel(
            id=row.id,
            name=row.name,
            type=row.type,
            parent_id=row.parent_id,
            category_name=None if is_thread else row.category_name,
            status=row.status,
            collect_enabled=row.collect_enabled,
        )

    result: list[PickerGuild] = []
    for guild_rows in guilds.values():
        threads = [r for r in guild_rows if r.type in THREAD_CHANNEL_TYPES]
        plain = sorted(
            (r for r in guild_rows if r.type not in THREAD_CHANNEL_TYPES),
            key=lambda r: (
                -1 if r.category_position is None else r.category_position,
                r.position if r.position is not None else 0,
                r.id,
            ),
        )
        listed = {r.id for r in plain}
        ordered: list[Any] = []
        for parent in plain:
            ordered.append(parent)
            ordered.extend(
                sorted((t for t in threads if t.parent_id == parent.id), key=lambda t: -t.id)
            )
        ordered.extend(
            sorted((t for t in threads if t.parent_id not in listed), key=lambda t: -t.id)
        )
        first = guild_rows[0]
        result.append(
            PickerGuild(
                id=first.guild_id,
                name=first.guild_name,
                status=first.guild_status,
                channels=tuple(picker(r) for r in ordered),
            )
        )
    return tuple(sorted(result, key=lambda g: (g.name.lower(), g.id)))


def _author_select():
    return select(
        DiscordUser.id, DiscordUser.username, DiscordUser.global_name, DiscordUser.avatar_hash
    )


def _has_messages():
    return exists().where(Message.author_id == DiscordUser.id)


def _author_rows(rows: Sequence[Any]) -> tuple[AuthorRow, ...]:
    return tuple(AuthorRow(r.id, r.username, r.global_name, r.avatar_hash) for r in rows)


async def suggest_authors(session: AsyncSession, query: str) -> tuple[AuthorRow, ...]:
    """不分大小写的子串匹配 username 和 global_name，只列发过消息的人（AC-9）。"""
    pattern = f"%{escape_like(query.lower())}%"
    rows = await session.execute(
        _author_select()
        .where(
            or_(
                func.lower(DiscordUser.username).like(pattern, escape="\\"),
                func.lower(DiscordUser.global_name).like(pattern, escape="\\"),
            ),
            _has_messages(),
        )
        .order_by(DiscordUser.username, DiscordUser.id)
        .limit(MAX_AUTHOR_SUGGESTIONS)
    )
    return _author_rows(rows.all())


async def authors_by_ids(session: AsyncSession, ids: Sequence[int]) -> tuple[AuthorRow, ...]:
    rows = await session.execute(_author_select().where(DiscordUser.id.in_(ids)))
    return _author_rows(rows.all())
