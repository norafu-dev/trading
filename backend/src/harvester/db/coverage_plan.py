"""覆盖段对齐（spec 0006）：比较“应该开着的”和“现在开着的”，差多少补多少。

`plan_coverage` 是纯函数；读 `desired` 和写入在同一个事务里（调用方开事务）。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from harvester.db.ingest import close_realtime_coverage, open_realtime_coverage
from harvester.db.models import AccountChannelAccess, Channel, ChannelCoverage, Guild
from harvester.db.snapshots import THREAD_CHANNEL_TYPES


@dataclass(frozen=True)
class CoveragePlan:
    to_open: frozenset[int]
    to_close: frozenset[int]


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


async def align_coverage(session: AsyncSession, account_id: int, at: datetime) -> CoveragePlan:
    plan = plan_coverage(
        await desired_channel_ids(session, account_id), await open_realtime_channel_ids(session)
    )
    for channel_id in sorted(plan.to_close):
        await close_realtime_coverage(session, channel_id, at)
    for channel_id in sorted(plan.to_open):
        await open_realtime_coverage(session, channel_id, at)
    return plan
