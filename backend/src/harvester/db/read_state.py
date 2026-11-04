"""频道已读位置和未读数（spec 0010）。这里的函数都不提交事务。

未读 = 比 `last_read_message_id` 新的消息，数据库现算；没有行等于 0 条未读。
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.models import Channel, ChannelReadState, DiscordAccount, Message

# 每个频道最多数到这么多条，接口和前端都把它当「很多」。
UNREAD_CAP = 100


@dataclass(frozen=True)
class UnreadCount:
    channel_id: int
    count: int


async def list_unread(session: AsyncSession) -> tuple[UnreadCount, ...]:
    """已开启采集、没删除、未读数大于 0 的频道。采集账号自己发的消息不算。

    账号的 `discord_user_id` 为 NULL 时 `IS DISTINCT FROM` 对所有作者都成立，即都数。
    """
    own_user_id = select(DiscordAccount.discord_user_id).scalar_subquery()
    recent = (
        select(Message.id)
        .where(
            Message.channel_id == ChannelReadState.channel_id,
            Message.id > ChannelReadState.last_read_message_id,
            Message.deleted_at.is_(None),
            Message.author_id.is_distinct_from(own_user_id),
        )
        .limit(UNREAD_CAP)
        .correlate(ChannelReadState)
        .lateral("recent")
    )
    count = func.count(recent.c.id)
    rows = await session.execute(
        select(ChannelReadState.channel_id, count.label("n"))
        .join(Channel, Channel.id == ChannelReadState.channel_id)
        .outerjoin(recent, true())
        .where(Channel.collect_enabled, Channel.status != "deleted")
        .group_by(ChannelReadState.channel_id)
        .having(count > 0)
        .order_by(ChannelReadState.channel_id)
    )
    return tuple(UnreadCount(channel_id=r.channel_id, count=r.n) for r in rows)


async def mark_read(
    session: AsyncSession, channel_id: int, message_id: int
) -> int | Literal["channel_not_found"]:
    """把已读位置推到「传入值和频道最新消息 ID 中较小的那个」，只往前推。返回推进后的位置。

    一条语句完成，所以旧标签页或乱序请求不会把位置退回去，写错的 ID 也不会超过最新消息。
    """
    if await session.scalar(select(Channel.id).where(Channel.id == channel_id)) is None:
        return "channel_not_found"
    latest = (
        select(func.coalesce(func.max(Message.id), 0))
        .where(Message.channel_id == channel_id)
        .scalar_subquery()
    )
    target = func.least(message_id, latest)
    insert = pg_insert(ChannelReadState).values(channel_id=channel_id, last_read_message_id=target)
    position = await session.scalar(
        insert.on_conflict_do_update(
            index_elements=["channel_id"],
            set_={
                "last_read_message_id": func.greatest(
                    ChannelReadState.last_read_message_id, insert.excluded.last_read_message_id
                ),
                "updated_at": func.now(),
            },
        ).returning(ChannelReadState.last_read_message_id)
    )
    assert position is not None
    return position
