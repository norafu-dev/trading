"""覆盖段对齐（spec 0006）：比较“应该开着的”和“现在开着的”，差多少补多少。

`plan_coverage` 是纯函数；读 `desired` 和写入在同一个事务里（调用方开事务）。
`banner_reason` 用同一组条件解释频道为什么没在采集（spec 0008），两边的测试共用一组数据。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from harvester.db.ingest import close_realtime_coverage, open_realtime_coverage
from harvester.db.models import AccountChannelAccess, Channel, ChannelCoverage, Guild
from harvester.db.snapshots import (
    THREAD_CHANNEL_TYPES,
    AccountStatus,
    ChannelStatus,
    GuildStatus,
)

BannerReason = Literal[
    "channel_deleted",
    "guild_left",
    "collect_off",
    "no_access",
    "thread_archived",
    "parent_unavailable",
    "account_reconnecting",
    "account_offline",
    "starting",
]


@dataclass(frozen=True)
class CoveragePlan:
    to_open: frozenset[int]
    to_close: frozenset[int]


@dataclass(frozen=True)
class CoverageAlignment:
    plan: CoveragePlan
    # 事务提交后开着实时段的频道，也就是监督循环判断消息存不存的名单。
    open_after: frozenset[int]


@dataclass(frozen=True)
class BannerFacts:
    """`desired_channel_ids` 看的那些条件，加上账号状态和有没有开放的实时段。"""

    channel_status: ChannelStatus
    guild_status: GuildStatus
    # 开了采集，并且归当前账号。
    collecting: bool
    can_read: bool
    is_thread: bool
    parent_active: bool
    parent_can_read: bool
    account_status: AccountStatus | None
    segment_open: bool


def banner_reason(facts: BannerFacts) -> BannerReason | None:
    """频道页的状态提示，取第一个符合的；顺序固定（AC-23）。"""
    if facts.channel_status == "deleted":
        return "channel_deleted"
    if facts.guild_status != "active":
        return "guild_left"
    if not facts.collecting:
        return "collect_off"
    if not facts.can_read:
        return "no_access"
    if facts.channel_status == "archived":
        return "thread_archived"
    if facts.is_thread and not (facts.parent_active and facts.parent_can_read):
        return "parent_unavailable"
    if facts.account_status == "reconnecting":
        return "account_reconnecting"
    if facts.account_status != "online":
        return "account_offline"
    return None if facts.segment_open else "starting"


def plan_coverage(desired: frozenset[int], open_now: frozenset[int]) -> CoveragePlan:
    return CoveragePlan(to_open=desired - open_now, to_close=open_now - desired)


async def desired_channel_ids(session: AsyncSession, account_id: int) -> frozenset[int]:
    """覆盖段应当开着的频道：开了采集、归这个账号、频道和服务器都 active、读得了；
    线程还要求父频道 active 且父频道也读得了。
    """
    channel = aliased(Channel)
    parent = aliased(Channel)
    access = aliased(AccountChannelAccess)
    parent_access = aliased(AccountChannelAccess)
    stmt = (
        select(channel.id)
        .join(Guild, Guild.id == channel.guild_id)
        .join(
            access,
            (access.channel_id == channel.id) & (access.account_id == account_id) & access.can_read,
        )
        .outerjoin(parent, parent.id == channel.parent_id)
        .outerjoin(
            parent_access,
            (parent_access.channel_id == parent.id) & (parent_access.account_id == account_id),
        )
        .where(
            channel.collect_enabled,
            channel.collector_account_id == account_id,
            channel.status == "active",
            Guild.status == "active",
            (channel.type.not_in(THREAD_CHANNEL_TYPES))
            | ((parent.status == "active") & parent_access.can_read),
        )
    )
    return frozenset((await session.scalars(stmt)).all())


async def open_realtime_channel_ids(session: AsyncSession) -> frozenset[int]:
    """所有开着的实时段，不限负责账号（兜住异常数据）。"""
    stmt = select(ChannelCoverage.channel_id).where(
        ChannelCoverage.source == "realtime", func.upper_inf(ChannelCoverage.span)
    )
    return frozenset((await session.scalars(stmt)).all())


async def subscription_guild_ids(session: AsyncSession, account_id: int) -> frozenset[int]:
    """要订阅的服务器：有开着采集的频道，或者有人点了「加载线程」（AC-9）。

    这里看开关，不看 `desired_channel_ids`：已归档的线程没有覆盖段，但服务器仍要订阅，
    才收得到它重新激活的事件。
    """
    enabled = select(Channel.guild_id).where(
        Channel.collect_enabled,
        Channel.collector_account_id == account_id,
        Channel.status != "deleted",
    )
    stmt = select(Guild.id).where(
        or_(Guild.id.in_(enabled), Guild.thread_load_requested_at.is_not(None))
    )
    return frozenset((await session.scalars(stmt)).all())


async def align_coverage(session: AsyncSession, account_id: int, at: datetime) -> CoverageAlignment:
    desired = await desired_channel_ids(session, account_id)
    plan = plan_coverage(desired, await open_realtime_channel_ids(session))
    for channel_id in sorted(plan.to_close):
        await close_realtime_coverage(session, channel_id, at)
    for channel_id in sorted(plan.to_open):
        await open_realtime_coverage(session, channel_id, at)
    return CoverageAlignment(plan, open_after=desired)
