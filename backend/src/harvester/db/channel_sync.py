"""collector 把服务器、频道和访问权限写进库（spec 0006）。这里的函数都不提交事务。

collector 只改元数据、`status` 和访问行，从不改 `collect_enabled`。
"""

from collections.abc import Iterable, Sequence
from datetime import datetime

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import set_guild_status, upsert_guild
from harvester.db.models import AccountChannelAccess, Channel, DiscordAccount, Guild
from harvester.db.raw import CHANNEL_COLLECT_CHANGED_CHANNEL, CHANNELS_CHANGED_CHANNEL
from harvester.db.snapshots import (
    CATEGORY_CHANNEL_TYPE,
    THREAD_CHANNEL_TYPES,
    ChannelSnapshot,
    GuildState,
)

log = structlog.get_logger("harvester.db.channel_sync")


def split_channels(
    channels: Iterable[ChannelSnapshot],
) -> tuple[tuple[ChannelSnapshot, ...], tuple[ChannelSnapshot, ...], tuple[ChannelSnapshot, ...]]:
    """分成分类、其余非线程频道、线程：父级必须先写，所以按这个顺序落库。"""
    items = tuple(channels)
    categories = tuple(c for c in items if c.type == CATEGORY_CHANNEL_TYPE)
    threads = tuple(c for c in items if c.type in THREAD_CHANNEL_TYPES)
    others = tuple(
        c for c in items if c.type != CATEGORY_CHANNEL_TYPE and c.type not in THREAD_CHANNEL_TYPES
    )
    return categories, others, threads


async def _existing_ids(session: AsyncSession, ids: Sequence[int]) -> frozenset[int]:
    if not ids:
        return frozenset()
    found = await session.scalars(select(Channel.id).where(Channel.id.in_(ids)))
    return frozenset(found.all())


# 一批频道用 unnest 数组一次写入：多行 VALUES 每次编译很慢，executemany 要几百次往返。
_UPSERT_CHANNELS = text(
    """
    INSERT INTO channel (id, guild_id, parent_id, type, name, position)
    SELECT * FROM unnest(
        CAST(:ids AS bigint[]), CAST(:guild_ids AS bigint[]), CAST(:parent_ids AS bigint[]),
        CAST(:types AS smallint[]), CAST(:names AS text[]), CAST(:positions AS integer[])
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name, type = EXCLUDED.type,
        parent_id = EXCLUDED.parent_id, position = EXCLUDED.position, updated_at = now()
    WHERE channel.name IS DISTINCT FROM EXCLUDED.name
       OR channel.type IS DISTINCT FROM EXCLUDED.type
       OR channel.parent_id IS DISTINCT FROM EXCLUDED.parent_id
       OR channel.position IS DISTINCT FROM EXCLUDED.position
    """
)

_UPSERT_ACCESS = text(
    """
    INSERT INTO account_channel_access (account_id, channel_id, can_read, checked_at)
    SELECT CAST(:account_id AS bigint), t.channel_id, t.can_read, CAST(:at AS timestamptz)
    FROM unnest(CAST(:channel_ids AS bigint[]), CAST(:can_reads AS boolean[]))
        AS t(channel_id, can_read)
    ON CONFLICT (account_id, channel_id) DO UPDATE
    SET can_read = EXCLUDED.can_read, checked_at = EXCLUDED.checked_at
    """
)

# 缺席表示失去权限，不表示被删（被删只认明确的删除事件）；线程缺席不处理。
_REVOKE_ABSENT = text(
    """
    UPDATE account_channel_access SET can_read = false, checked_at = CAST(:at AS timestamptz)
    WHERE account_id = :account_id AND can_read
      AND channel_id IN (
          SELECT id FROM channel WHERE guild_id = :guild_id AND type <> ALL(:thread_types)
      )
      AND channel_id <> ALL(CAST(:present_ids AS bigint[]))
    """
)


async def _upsert_batch(
    session: AsyncSession, batch: Sequence[ChannelSnapshot], written: frozenset[int]
) -> frozenset[int]:
    """一批频道一条语句 upsert；父级既不在库里也不在已写集合里的行跳过并记日志。"""
    parent_ids = tuple({c.parent_id for c in batch if c.parent_id is not None} - written)
    known = written | await _existing_ids(session, parent_ids)
    rows = [c for c in batch if c.parent_id is None or c.parent_id in known]
    if len(rows) < len(batch):
        kept = {c.id for c in rows}
        for skipped in (c for c in batch if c.id not in kept):
            log.warning(
                "channel_sync.missing_parent", channel_id=skipped.id, parent_id=skipped.parent_id
            )
    if not rows:
        return frozenset()
    await session.execute(
        _UPSERT_CHANNELS,
        {
            "ids": [c.id for c in rows],
            "guild_ids": [c.guild_id for c in rows],
            "parent_ids": [c.parent_id for c in rows],
            "types": [c.type for c in rows],
            "names": [c.name for c in rows],
            "positions": [c.position for c in rows],
        },
    )
    return frozenset(c.id for c in rows)


async def _write_access(
    session: AsyncSession,
    account_id: int,
    state: GuildState,
    written: frozenset[int],
    at: datetime,
) -> None:
    rows = [a for a in state.access if a.channel_id in written]
    if rows:
        await session.execute(
            _UPSERT_ACCESS,
            {
                "account_id": account_id,
                "at": at,
                "channel_ids": [a.channel_id for a in rows],
                "can_reads": [a.can_read for a in rows],
            },
        )
    await session.execute(
        _REVOKE_ABSENT,
        {
            "at": at,
            "account_id": account_id,
            "guild_id": state.guild.id,
            "thread_types": list(THREAD_CHANNEL_TYPES),
            "present_ids": [c.id for c in state.channels],
        },
    )


async def apply_guild_state(
    session: AsyncSession, account_id: int, state: GuildState, at: datetime
) -> None:
    """一个服务器的快照落库：服务器、分类、频道、线程，再写访问行。"""
    await upsert_guild(session, state.guild)
    await set_guild_status(session, state.guild.id, "active", at)
    categories, others, threads = split_channels(state.channels)
    written: frozenset[int] = frozenset()
    for batch in (categories, others, threads):
        written |= await _upsert_batch(session, batch, written)
    # 自己的成员信息缺失时访问权限算不出来，沿用旧值，包括不执行“缺席写假”。
    if state.me_known:
        await _write_access(session, account_id, state, written, at)
    else:
        log.warning("channel_sync.me_unknown", guild_id=state.guild.id)


async def leave_guild(session: AsyncSession, account_id: int, guild_id: int, at: datetime) -> None:
    """退出或被移出服务器：服务器改成 left，里面所有频道的 can_read 写成假，消息不动。"""
    await set_guild_status(session, guild_id, "left", at)
    await session.execute(
        update(AccountChannelAccess)
        .where(
            AccountChannelAccess.account_id == account_id,
            AccountChannelAccess.can_read,
            AccountChannelAccess.channel_id.in_(
                select(Channel.id).where(Channel.guild_id == guild_id)
            ),
        )
        .values(can_read=False, checked_at=at)
    )


async def mark_missing_guilds_left(
    session: AsyncSession, account_id: int, present_ids: frozenset[int], at: datetime
) -> int:
    """库里 active 但这次登录看不到的服务器改成 left；不可用的服务器要包含在 present_ids 里。"""
    active = await session.scalars(select(Guild.id).where(Guild.status == "active"))
    missing = [guild_id for guild_id in active.all() if guild_id not in present_ids]
    for guild_id in missing:
        await leave_guild(session, account_id, guild_id, at)
    return len(missing)


async def record_synced(
    session: AsyncSession, account_id: int, expected_version: int, at: datetime
) -> bool:
    """写 channels_synced_at，带版本校验：换身份后一次在路上的旧写入不会把它写回来。"""
    written = await session.scalar(
        update(DiscordAccount)
        .where(DiscordAccount.id == account_id, DiscordAccount.config_version == expected_version)
        .values(channels_synced_at=at)
        .returning(DiscordAccount.id)
    )
    return written is not None


async def notify_channels_changed(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_notify(:channel, '{}')"), {"channel": CHANNELS_CHANGED_CHANNEL}
    )


async def notify_collect_changed(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_notify(:channel, '{}')"), {"channel": CHANNEL_COLLECT_CHANGED_CHANNEL}
    )
