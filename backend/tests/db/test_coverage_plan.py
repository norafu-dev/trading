"""AC-7、AC-8：覆盖段对齐（真实 PostgreSQL）。"""

from dataclasses import replace

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import channel_sync
from harvester.db.coverage_plan import (
    BannerFacts,
    BannerReason,
    align_coverage,
    banner_reason,
    desired_channel_ids,
    plan_coverage,
    subscription_guild_ids,
)
from harvester.db.models import AccountChannelAccess, Channel, ChannelCoverage, Guild
from harvester.db.snapshots import AccessSnapshot, ChannelStatus, GuildState
from tests.db.builders import CHANNEL, GUILD, at, make_account
from tests.db.test_channel_sync import FORUM, POST, STATE, THREAD


def test_plan_coverage_opens_missing_and_closes_extra() -> None:
    plan = plan_coverage(frozenset({1, 2, 3}), frozenset({3, 4}))

    assert plan.to_open == frozenset({1, 2})
    assert plan.to_close == frozenset({4})


def test_plan_coverage_is_empty_when_aligned() -> None:
    plan = plan_coverage(frozenset({1}), frozenset({1}))

    assert not plan.to_open and not plan.to_close


async def enable(session: AsyncSession, account_id: int, *channel_ids: int) -> None:
    await session.execute(
        update(Channel)
        .where(Channel.id.in_(channel_ids))
        .values(collect_enabled=True, collector_account_id=account_id)
    )


async def seeded(session: AsyncSession) -> int:
    account_id = await make_account(session)
    await channel_sync.apply_guild_state(session, account_id, STATE, at(0))
    return account_id


async def open_ids(session: AsyncSession) -> set[int]:
    rows = await session.scalars(
        select(ChannelCoverage.channel_id).where(ChannelCoverage.span.op("@>")(at(100)))
    )
    return set(rows)


async def test_enabled_readable_channel_is_desired(session: AsyncSession) -> None:  # AC-7
    account_id = await seeded(session)
    await enable(session, account_id, CHANNEL.id)

    assert await desired_channel_ids(session, account_id) == {CHANNEL.id}


async def test_channel_owned_by_nobody_is_not_desired(session: AsyncSession) -> None:  # AC-7
    account_id = await seeded(session)
    await session.execute(
        update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=True)
    )

    assert await desired_channel_ids(session, account_id) == set()


async def test_unreadable_deleted_or_left_is_not_desired(session: AsyncSession) -> None:  # AC-8
    account_id = await seeded(session)
    await enable(session, account_id, CHANNEL.id)

    await session.execute(update(AccountChannelAccess).values(can_read=False))
    assert await desired_channel_ids(session, account_id) == set()

    await session.execute(update(AccountChannelAccess).values(can_read=True))
    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status="deleted"))
    assert await desired_channel_ids(session, account_id) == set()

    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status="active"))
    await session.execute(update(Guild).values(status="left"))
    assert await desired_channel_ids(session, account_id) == set()


async def test_thread_needs_its_parent_readable_and_active(session: AsyncSession) -> None:  # AC-8
    account_id = await seeded(session)
    await enable(session, account_id, THREAD.id)
    assert await desired_channel_ids(session, account_id) == {THREAD.id}

    await session.execute(
        update(AccountChannelAccess)
        .where(AccountChannelAccess.channel_id == CHANNEL.id)
        .values(can_read=False)
    )
    assert await desired_channel_ids(session, account_id) == set()

    await session.execute(
        update(AccountChannelAccess)
        .where(AccountChannelAccess.channel_id == CHANNEL.id)
        .values(can_read=True)
    )
    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status="deleted"))
    assert await desired_channel_ids(session, account_id) == set()


async def test_forum_post_follows_the_forum_access(session: AsyncSession) -> None:
    # 论坛帖子的父频道是论坛，论坛也有访问行，所以帖子可以被采集。
    account_id = await seeded(session)
    state = GuildState(
        GUILD,
        STATE.channels,
        (AccessSnapshot(FORUM.id, True), AccessSnapshot(POST.id, True)),
    )
    await channel_sync.apply_guild_state(session, account_id, state, at(1))
    await enable(session, account_id, POST.id)

    assert await desired_channel_ids(session, account_id) == {POST.id}


async def test_align_opens_closes_and_survives_permission_round_trip(
    session: AsyncSession,
) -> None:  # AC-7、AC-8
    account_id = await seeded(session)
    await enable(session, account_id, CHANNEL.id)

    alignment = await align_coverage(session, account_id, at(1))
    assert alignment.plan.to_open == {CHANNEL.id}
    assert alignment.open_after == {CHANNEL.id}
    assert await open_ids(session) == {CHANNEL.id}

    # 重复对齐没有变化。
    again = await align_coverage(session, account_id, at(2))
    assert not again.plan.to_open and not again.plan.to_close

    # 失去权限：覆盖段关闭，开关不变。
    await session.execute(update(AccountChannelAccess).values(can_read=False))
    await align_coverage(session, account_id, at(3))
    assert await open_ids(session) == set()
    assert await session.scalar(select(Channel.collect_enabled).where(Channel.id == 201)) is True

    # 权限恢复：重新开一段。
    await session.execute(update(AccountChannelAccess).values(can_read=True))
    await align_coverage(session, account_id, at(4))
    spans = list(await session.scalars(select(ChannelCoverage.span).order_by(ChannelCoverage.id)))
    assert [(s.lower, s.upper) for s in spans] == [(at(1), at(3)), (at(4), None)]


async def test_align_closes_segment_when_switch_is_turned_off(session: AsyncSession) -> None:
    account_id = await seeded(session)
    await enable(session, account_id, CHANNEL.id)
    await align_coverage(session, account_id, at(1))

    await session.execute(update(Channel).values(collect_enabled=False))
    alignment = await align_coverage(session, account_id, at(2))

    assert alignment.plan.to_close == {CHANNEL.id}
    assert alignment.open_after == frozenset()
    assert await open_ids(session) == set()


HEALTHY = BannerFacts(
    channel_status="active",
    guild_status="active",
    collecting=True,
    can_read=True,
    is_thread=False,
    parent_active=False,
    parent_can_read=False,
    account_status="online",
    segment_open=True,
)
THREAD_OK = replace(HEALTHY, is_thread=True, parent_active=True, parent_can_read=True)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (HEALTHY, None),
        (THREAD_OK, None),
        (replace(HEALTHY, segment_open=False), "starting"),
        (replace(HEALTHY, account_status="connecting"), "account_offline"),
        (replace(HEALTHY, account_status=None), "account_offline"),
        # 重连时覆盖段可能还开着，也要提示。
        (replace(HEALTHY, account_status="reconnecting"), "account_reconnecting"),
        (replace(THREAD_OK, parent_can_read=False, segment_open=False), "parent_unavailable"),
        (replace(THREAD_OK, parent_active=False, segment_open=False), "parent_unavailable"),
        (replace(THREAD_OK, channel_status="archived"), "thread_archived"),
        (replace(HEALTHY, can_read=False), "no_access"),
        (replace(HEALTHY, collecting=False), "collect_off"),
        (replace(HEALTHY, guild_status="left"), "guild_left"),
        (replace(HEALTHY, guild_status="deleted"), "guild_left"),
        (replace(HEALTHY, channel_status="deleted"), "channel_deleted"),
        # 顺序：取第一个符合的。
        (replace(HEALTHY, channel_status="deleted", guild_status="left"), "channel_deleted"),
        (replace(HEALTHY, guild_status="left", collecting=False), "guild_left"),
        (replace(HEALTHY, collecting=False, can_read=False), "collect_off"),
        (replace(HEALTHY, can_read=False, channel_status="archived"), "no_access"),
        (replace(THREAD_OK, channel_status="archived", parent_active=False), "thread_archived"),
        (
            replace(THREAD_OK, parent_active=False, account_status="connecting"),
            "parent_unavailable",
        ),
    ],
)
def test_banner_reason_order(facts: BannerFacts, expected: BannerReason | None) -> None:  # AC-23
    assert banner_reason(facts) == expected


async def test_subscription_covers_enabled_and_requested_guilds(
    session: AsyncSession,
) -> None:  # AC-9
    account_id = await seeded(session)
    assert await subscription_guild_ids(session, account_id) == frozenset()

    await enable(session, account_id, CHANNEL.id)
    assert await subscription_guild_ids(session, account_id) == {GUILD.id}

    # 已归档的线程没有覆盖段，服务器仍要订阅。
    status: ChannelStatus = "archived"
    await session.execute(update(Channel).where(Channel.id == CHANNEL.id).values(status=status))
    assert await subscription_guild_ids(session, account_id) == {GUILD.id}
    assert await desired_channel_ids(session, account_id) == frozenset()

    await session.execute(update(Channel).values(status="deleted"))
    assert await subscription_guild_ids(session, account_id) == frozenset()

    await session.execute(update(Guild).values(thread_load_requested_at=at(1)))
    assert await subscription_guild_ids(session, account_id) == {GUILD.id}
