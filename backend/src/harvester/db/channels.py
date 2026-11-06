"""频道管理页的读写（spec 0006）。这里的函数都不提交事务，可预期的结果用返回值表达。

`collect_enabled` 只由这里改；写操作和通知在同一个事务里。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Row, and_, case, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import account as accounts
from harvester.db.channel_sync import notify_channels_changed, notify_collect_changed
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelReadState,
    DiscordAccount,
    Guild,
    Message,
)
from harvester.db.snapshots import (
    CATEGORY_CHANNEL_TYPE,
    COLLECTABLE_CHANNEL_TYPES,
    THREAD_CHANNEL_TYPES,
    ChannelStatus,
    GuildStatus,
)

# 管理页能看到的类型：分类、文字、公告、论坛、媒体加线程；语音和舞台不显示。
LISTED_CHANNEL_TYPES = (CATEGORY_CHANNEL_TYPE, 0, 5, 15, 16, *THREAD_CHANNEL_TYPES)

CollectError = Literal[
    "channel_not_found",
    "account_not_found",
    "channel_not_collectable",
    "channel_no_access",
    "channel_unavailable",
]


CategoryCollectError = Literal["category_not_found", "not_a_category", "account_not_found"]
ThreadLoadError = Literal["guild_not_found", "account_not_found", "guild_unavailable"]


@dataclass(frozen=True)
class CategoryCollectResult:
    changed: int
    skipped_no_access: int


@dataclass(frozen=True)
class GuildBrief:
    id: int
    name: str
    icon_hash: str | None


@dataclass(frozen=True)
class GuildRow:
    id: int
    name: str
    icon_hash: str | None
    status: GuildStatus
    enabled_count: int
    thread_load_pending: bool


@dataclass(frozen=True)
class ChannelRow:
    id: int
    guild_id: int
    parent_id: int | None
    type: int
    name: str
    position: int | None
    status: ChannelStatus
    collect_enabled: bool
    collectable: bool
    can_read: bool


def _can_read_column():  # SQLAlchemy 的标签列类型太长，这里不标注。
    return exists(
        select(1).where(
            AccountChannelAccess.channel_id == Channel.id, AccountChannelAccess.can_read
        )
    ).label("can_read")


def _channel_select():
    return select(
        Channel.id,
        Channel.guild_id,
        Channel.parent_id,
        Channel.type,
        Channel.name,
        Channel.position,
        Channel.status,
        Channel.collect_enabled,
        _can_read_column(),
    )


def _to_channel_row(
    row: Row[int, int, int | None, int, str, int | None, ChannelStatus, bool, bool],
) -> ChannelRow:
    return ChannelRow(
        id=row.id,
        guild_id=row.guild_id,
        parent_id=row.parent_id,
        type=row.type,
        name=row.name,
        position=row.position,
        status=row.status,
        collect_enabled=row.collect_enabled,
        collectable=row.type in COLLECTABLE_CHANNEL_TYPES,
        can_read=row.can_read,
    )


async def get_synced_at(session: AsyncSession) -> datetime | None:
    return await session.scalar(select(DiscordAccount.channels_synced_at))


async def list_guilds(session: AsyncSession) -> tuple[GuildRow, ...]:
    """退出的服务器排在最后；已开启的数量把失去权限但仍开启的频道也算进去。"""
    enabled = (
        select(func.count())
        .where(
            Channel.guild_id == Guild.id,
            Channel.collect_enabled,
            Channel.status == "active",
        )
        .scalar_subquery()
    )
    rows = await session.execute(
        select(
            Guild.id,
            Guild.name,
            Guild.icon_hash,
            Guild.status,
            enabled.label("enabled_count"),
            Guild.thread_load_requested_at,
        )
        .where(Guild.status.in_(("active", "left")))
        .order_by(case((Guild.status == "left", 1), else_=0), func.lower(Guild.name), Guild.id)
    )
    return tuple(
        GuildRow(
            r.id,
            r.name,
            r.icon_hash,
            r.status,
            r.enabled_count,  # pyright: ignore[reportArgumentType]
            r.thread_load_requested_at is not None,
        )
        for r in rows
    )


async def list_guild_channels(
    session: AsyncSession, guild_id: int
) -> tuple[ChannelRow, ...] | Literal["guild_not_found"]:
    """active 的可显示频道，加上已开启的 archived 线程；语音、舞台和 deleted 的不返回。"""
    if await session.scalar(select(Guild.id).where(Guild.id == guild_id)) is None:
        return "guild_not_found"
    rows = await session.execute(
        _channel_select()
        .where(
            Channel.guild_id == guild_id,
            Channel.type.in_(LISTED_CHANNEL_TYPES),
            or_(
                Channel.status == "active",
                and_(
                    Channel.status == "archived",
                    Channel.collect_enabled,
                    Channel.type.in_(THREAD_CHANNEL_TYPES),
                ),
            ),
        )
        .order_by(Channel.position.asc().nulls_last(), func.lower(Channel.name), Channel.id)
    )
    return tuple(_to_channel_row(r) for r in rows)


async def _get_channel_row(session: AsyncSession, channel_id: int) -> ChannelRow:
    row = (await session.execute(_channel_select().where(Channel.id == channel_id))).one()
    return _to_channel_row(row)


async def _enable_blocker(
    session: AsyncSession, channel: Channel, account: accounts.AccountRow
) -> CollectError | None:
    """打开前的检查，顺序固定：类型、可用性、读取权限。"""
    if channel.type not in COLLECTABLE_CHANNEL_TYPES:
        return "channel_not_collectable"
    guild_status = await session.scalar(select(Guild.status).where(Guild.id == channel.guild_id))
    if channel.status == "deleted" or guild_status != "active":
        return "channel_unavailable"
    can_read = await session.scalar(
        select(AccountChannelAccess.can_read).where(
            AccountChannelAccess.account_id == account.id,
            AccountChannelAccess.channel_id == channel.id,
        )
    )
    if can_read is False:
        return "channel_no_access"
    # 没有访问行：从未同步或换身份后还没重新同步时放行，同步后由覆盖对齐决定是否开段。
    if can_read is None and await get_synced_at(session) is not None:
        return "channel_no_access"
    return None


async def init_read_state(session: AsyncSession, channel_ids: Sequence[int]) -> None:
    """给还没有已读记录的频道建一行，位置是当时最新的消息 ID（没有消息则 0）。

    已有记录的频道不改，所以关闭再开启不会重置已读位置（spec 0010 AC-2）。
    """
    if not channel_ids:
        return
    latest = (
        select(func.coalesce(func.max(Message.id), 0))
        .where(Message.channel_id == Channel.id)
        .scalar_subquery()
    )
    await session.execute(
        pg_insert(ChannelReadState)
        .from_select(
            ["channel_id", "last_read_message_id"],
            select(Channel.id, latest).where(Channel.id.in_(channel_ids)),
        )
        .on_conflict_do_nothing(index_elements=["channel_id"])
    )


async def set_collect(
    session: AsyncSession, channel_id: int, enabled: bool
) -> ChannelRow | CollectError:
    """打开或关闭一个频道的采集。已经是目标值时不写也不通知；关闭不会被拦住。"""
    channel = await session.scalar(
        select(Channel).where(Channel.id == channel_id).with_for_update()
    )
    if channel is None:
        return "channel_not_found"
    if channel.collect_enabled == enabled:
        return await _get_channel_row(session, channel_id)
    values: dict[str, object] = {"collect_enabled": enabled, "updated_at": func.now()}
    if enabled:
        account = await accounts.get_account(session)
        if account is None:
            return "account_not_found"
        blocker = await _enable_blocker(session, channel, account)
        if blocker is not None:
            return blocker
        values["collector_account_id"] = account.id
    await session.execute(update(Channel).where(Channel.id == channel_id).values(**values))
    if enabled:
        await init_read_state(session, [channel_id])
    await notify_collect_changed(session)
    await notify_channels_changed(session)
    return await _get_channel_row(session, channel_id)


async def set_category_collect(
    session: AsyncSession, category_id: int, enabled: bool
) -> CategoryCollectResult | CategoryCollectError:
    """一次开关分类的直接子频道（不含线程）。一个事务，只在值真的变化时才计数和通知。"""
    category = await session.scalar(select(Channel).where(Channel.id == category_id))
    if category is None:
        return "category_not_found"
    if category.type != CATEGORY_CHANNEL_TYPE:
        return "not_a_category"
    account = await accounts.get_account(session)
    if enabled and account is None:
        return "account_not_found"
    children = (
        await session.scalars(
            select(Channel)
            .where(
                Channel.parent_id == category_id,
                Channel.type.in_(COLLECTABLE_CHANNEL_TYPES),
                Channel.status == "active",
            )
            .with_for_update()
        )
    ).all()
    if not enabled:
        changed = [c.id for c in children if c.collect_enabled]
        skipped = 0
        values: dict[str, object] = {"collect_enabled": False, "updated_at": func.now()}
    else:
        assert account is not None
        guild_status = await session.scalar(
            select(Guild.status).where(Guild.id == category.guild_id)
        )
        if guild_status != "active":
            return CategoryCollectResult(changed=0, skipped_no_access=len(children))
        readable = await _readable_ids(session, account.id, [c.id for c in children])
        pending = [c.id for c in children if not c.collect_enabled]
        changed = [channel_id for channel_id in pending if channel_id in readable]
        skipped = len(pending) - len(changed)
        values = {
            "collect_enabled": True,
            "collector_account_id": account.id,
            "updated_at": func.now(),
        }
    if changed:
        await session.execute(update(Channel).where(Channel.id.in_(changed)).values(**values))
        if enabled:
            await init_read_state(session, changed)
        await notify_collect_changed(session)
        await notify_channels_changed(session)
    return CategoryCollectResult(changed=len(changed), skipped_no_access=skipped)


async def _readable_ids(
    session: AsyncSession, account_id: int, channel_ids: list[int]
) -> frozenset[int]:
    """规则和单个开关相同：有访问行看 can_read；没有访问行时，只有从未同步才放行。"""
    rows = await session.execute(
        select(AccountChannelAccess.channel_id, AccountChannelAccess.can_read).where(
            AccountChannelAccess.account_id == account_id,
            AccountChannelAccess.channel_id.in_(channel_ids),
        )
    )
    access = {channel_id: can_read for channel_id, can_read in rows}
    never_synced = await get_synced_at(session) is None
    return frozenset(c for c in channel_ids if access.get(c, never_synced))


async def request_thread_load(
    session: AsyncSession, guild_id: int, at: datetime
) -> ThreadLoadError | None:
    """记下「加载线程」的请求并叫醒 collector。幂等：重复点不改时间也不再通知。"""
    guild_status = await session.scalar(
        select(Guild.status).where(Guild.id == guild_id).with_for_update()
    )
    if guild_status is None:
        return "guild_not_found"
    if await accounts.get_account(session) is None:
        return "account_not_found"
    if guild_status != "active":
        return "guild_unavailable"
    requested = await session.scalar(
        update(Guild)
        .where(Guild.id == guild_id, Guild.thread_load_requested_at.is_(None))
        .values(thread_load_requested_at=at)
        .returning(Guild.id)
    )
    if requested is not None:
        await notify_collect_changed(session)
        await notify_channels_changed(session)
    return None


async def list_collected(
    session: AsyncSession,
) -> tuple[tuple[GuildBrief, ...], tuple[ChannelRow, ...]]:
    """已开启的频道，加上侧栏分组要用的分类和父频道（线程的父频道还可能有分类）。"""
    found: dict[int, ChannelRow] = {}
    rows = await session.execute(
        _channel_select().where(Channel.collect_enabled, Channel.status != "deleted")
    )
    current = [_to_channel_row(r) for r in rows]
    while current:
        found |= {c.id: c for c in current}
        parent_ids = {c.parent_id for c in current if c.parent_id is not None} - found.keys()
        if not parent_ids:
            break
        rows = await session.execute(_channel_select().where(Channel.id.in_(parent_ids)))
        current = [_to_channel_row(r) for r in rows]
    guild_ids = {c.guild_id for c in found.values() if c.collect_enabled}
    guilds = await session.execute(
        select(Guild.id, Guild.name, Guild.icon_hash).where(Guild.id.in_(guild_ids))
    )
    return (
        tuple(GuildBrief(g.id, g.name, g.icon_hash) for g in guilds),
        tuple(found.values()),
    )
