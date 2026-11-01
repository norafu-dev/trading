"""AC-7、AC-8：覆盖段对齐（真实 PostgreSQL）。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import channel_sync
from harvester.db.coverage_plan import align_coverage, desired_channel_ids, plan_coverage
from harvester.db.models import AccountChannelAccess, Channel, ChannelCoverage, Guild
from harvester.db.snapshots import AccessSnapshot, GuildState
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

    plan = await align_coverage(session, account_id, at(1))
    assert plan.to_open == {CHANNEL.id}
    assert await open_ids(session) == {CHANNEL.id}

    # 重复对齐没有变化。
    again = await align_coverage(session, account_id, at(2))
    assert not again.to_open and not again.to_close

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
    plan = await align_coverage(session, account_id, at(2))

    assert plan.to_close == {CHANNEL.id}
    assert await open_ids(session) == set()
