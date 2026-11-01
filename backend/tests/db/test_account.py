"""账号表的读写和登录额度（spec 0005，真实 PostgreSQL）。"""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.db import account as accounts
from harvester.db.account import AccountRow, LoginBudgetExhausted
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    DiscordAccount,
    DiscordLoginAttempt,
)
from tests.db.builders import CHANNEL, at, seed_channel


async def new_account(session: AsyncSession) -> AccountRow:
    row = await accounts.create_account(session, "main", b"cipher", 1)
    assert row != "exists"
    return row


async def add_attempts(session: AsyncSession, account_id: int, *hours_ago: float) -> None:
    for hours in hours_ago:
        await session.execute(
            insert(DiscordLoginAttempt).values(
                account_id=account_id,
                attempted_at=text(f"now() - interval '{hours} hours'"),
            )
        )


async def test_concurrent_create_keeps_one_row(engine: AsyncEngine) -> None:  # covers: AC-3
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create() -> object:
        async with factory() as session, session.begin():
            return await accounts.create_account(session, "main", b"cipher", 1)

    results = await asyncio.gather(create(), create())

    assert sorted(r == "exists" for r in results) == [False, True]
    async with factory() as session:
        assert len((await session.scalars(select(DiscordAccount))).all()) == 1


async def test_budget_counts_the_rolling_window(session: AsyncSession) -> None:  # covers: AC-7
    row = await new_account(session)
    await add_attempts(session, row.id, 25, *([1] * 9))

    assert await accounts.check_login_budget(session, row.id) == "available"
    assert await accounts.reserve_login_attempt(session, row.id) == "reserved"

    exhausted = await accounts.reserve_login_attempt(session, row.id)
    assert isinstance(exhausted, LoginBudgetExhausted)
    assert isinstance(await accounts.check_login_budget(session, row.id), LoginBudgetExhausted)
    count = await session.scalar(select(text("count(*)")).select_from(DiscordLoginAttempt))
    assert count == 11  # 25 小时前的 1 行加窗口内 10 行，第 11 次没有写入。


async def test_retry_at_is_earliest_attempt_plus_window(session: AsyncSession) -> None:  # AC-7
    row = await new_account(session)
    await add_attempts(session, row.id, 5, *([1] * 9))

    exhausted = await accounts.check_login_budget(session, row.id)

    assert isinstance(exhausted, LoginBudgetExhausted)
    earliest = await session.scalar(
        select(text("min(attempted_at)")).select_from(DiscordLoginAttempt)
    )
    assert exhausted.retry_at == earliest + timedelta(hours=24)


async def test_concurrent_reservations_never_exceed_budget(engine: AsyncEngine) -> None:  # AC-7
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        row = await new_account(session)
        await add_attempts(session, row.id, *([1] * 8))

    async def reserve() -> object:
        async with factory() as session, session.begin():
            return await accounts.reserve_login_attempt(session, row.id)

    results = await asyncio.gather(*(reserve() for _ in range(5)))

    assert results.count("reserved") == 2


async def test_reserve_purges_rows_older_than_retention(session: AsyncSession) -> None:
    row = await new_account(session)
    await add_attempts(session, row.id, 24 * 8, 1)

    await accounts.reserve_login_attempt(session, row.id)

    count = await session.scalar(select(text("count(*)")).select_from(DiscordLoginAttempt))
    assert count == 2


async def test_stale_version_loses_to_api_write(session: AsyncSession) -> None:  # covers: AC-13
    row = await new_account(session)
    disabled = await accounts.set_enabled(session, False)
    assert isinstance(disabled, AccountRow)
    assert disabled.config_version == row.config_version + 1

    result = await accounts.record_status(
        session, row.id, row.config_version, "reconnecting", "network_error"
    )

    assert result == "stale_version"
    current = await accounts.get_account(session)
    assert current is not None and current.status == "disabled"


async def test_status_changed_at_moves_only_on_change(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        row = await new_account(session)
        await accounts.record_status(session, row.id, 1, "reconnecting", "network_error")
    async with factory() as session:
        first = await accounts.get_account(session)
    async with factory() as session, session.begin():
        await accounts.record_status(session, row.id, 1, "reconnecting", "http_5xx")
    async with factory() as session:
        second = await accounts.get_account(session)
    assert first is not None and second is not None
    assert second.last_error == "http_5xx"
    assert second.status_changed_at == first.status_changed_at


async def test_online_clears_error_and_writes_heartbeat(session: AsyncSession) -> None:
    row = await new_account(session)
    await accounts.record_status(session, row.id, 1, "reconnecting", "network_error")
    await accounts.record_status(session, row.id, 1, "online", None)

    current = await accounts.get_account(session)

    assert current is not None
    assert current.last_error is None
    assert current.last_heartbeat_at is not None


async def test_login_paused_requires_retry_at(session: AsyncSession) -> None:
    row = await new_account(session)
    with pytest.raises(IntegrityError):
        await accounts.record_status(session, row.id, 1, "login_paused", "login_budget_exhausted")


async def test_identity_written_then_compared(session: AsyncSession) -> None:  # covers: AC-10
    row = await new_account(session)

    assert await accounts.record_identity(session, row.id, 1, 111, "first") == "updated"
    assert await accounts.record_identity(session, row.id, 1, 111, "renamed") == "updated"
    assert await accounts.record_identity(session, row.id, 1, 222, "other") == "mismatch"
    assert await accounts.record_identity(session, row.id, 9, 111, "x") == "stale_version"

    current = await accounts.get_account(session)
    assert current is not None
    assert (current.discord_user_id, current.discord_username) == (111, "renamed")


async def test_switch_identity_only_touches_identity_and_access(
    session: AsyncSession,
) -> None:  # covers: AC-11
    await seed_channel(session)
    row = await new_account(session)
    await accounts.record_identity(session, row.id, 1, 111, "first")
    await session.execute(
        insert(AccountChannelAccess).values(account_id=row.id, channel_id=CHANNEL.id, can_read=True)
    )
    await session.execute(
        update(Channel)
        .where(Channel.id == CHANNEL.id)
        .values(collector_account_id=row.id, collect_enabled=True)
    )

    assert await accounts.switch_identity(session) == "invalid_state"
    await accounts.record_status(session, row.id, 1, "account_mismatch", "account_mismatch")
    switched = await accounts.switch_identity(session)

    assert isinstance(switched, AccountRow)
    assert switched.status == "connecting"
    assert switched.discord_user_id is None and switched.discord_username is None
    access = await session.scalars(select(AccountChannelAccess))
    assert access.all() == []
    channel = (
        await session.execute(
            select(Channel.collect_enabled, Channel.collector_account_id).where(
                Channel.id == CHANNEL.id
            )
        )
    ).one()
    assert tuple(channel) == (True, row.id)


async def test_replace_token_while_disabled_keeps_disabled(session: AsyncSession) -> None:  # AC-12
    await new_account(session)
    await accounts.set_enabled(session, False)

    replaced = await accounts.replace_token(session, b"new", 1)

    assert isinstance(replaced, AccountRow)
    assert replaced.status == "disabled"
    assert replaced.token_ciphertext == b"new"


async def test_disable_twice_does_not_bump_version(session: AsyncSession) -> None:
    await new_account(session)
    first = await accounts.set_enabled(session, False)
    second = await accounts.set_enabled(session, False)

    assert isinstance(first, AccountRow) and isinstance(second, AccountRow)
    assert first.config_version == second.config_version


async def test_coverage_helpers_follow_collect_flag(session: AsyncSession) -> None:
    await seed_channel(session)
    row = await new_account(session)
    assert await accounts.open_account_coverage(session, row.id, at(0)) == 0

    await session.execute(
        update(Channel)
        .where(Channel.id == CHANNEL.id)
        .values(collector_account_id=row.id, collect_enabled=True)
    )
    assert await accounts.open_account_coverage(session, row.id, at(0)) == 1
    assert await accounts.close_account_coverage(session, row.id, at(5)) == 1

    span = await session.scalar(select(ChannelCoverage.span))
    assert span is not None and span.upper == at(5)


def _row(**overrides: object) -> AccountRow:
    base: dict[str, object] = {
        "id": 1,
        "label": "main",
        "token_ciphertext": b"",
        "discord_user_id": None,
        "discord_username": None,
        "status": "online",
        "status_changed_at": at(0),
        "last_error": None,
        "last_heartbeat_at": at(0),
        "retry_at": None,
        "config_version": 1,
        "db_now": at(0) + timedelta(seconds=121),
    }
    return AccountRow(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


def test_collector_stale_rules() -> None:  # covers: AC-15
    assert accounts.is_collector_stale(_row())
    assert not accounts.is_collector_stale(_row(last_heartbeat_at=None))
    assert accounts.is_collector_stale(_row(status="connecting"))
    assert not accounts.is_collector_stale(_row(status="reconnecting"))
    assert not accounts.is_collector_stale(_row(db_now=at(0) + timedelta(seconds=120)))
