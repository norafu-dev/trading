"""collector 进程入口：真实 PostgreSQL 的锁和 NOTIFY，假的 Discord 会话（AC-1、AC-14、AC-17）。"""

import asyncio
import os
import signal
import time
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.collector import main
from harvester.collector.events import SessionEnded
from harvester.collector.wake_guard import WakeGuard
from harvester.crypto import PURPOSE_DISCORD_TOKEN, encrypt
from harvester.db import account as accounts
from harvester.db import raw
from harvester.db.account import AccountRow
from harvester.db.models import AccountChannelAccess, Channel, ChannelCoverage
from harvester.db.snapshots import GuildState
from tests.api.conftest import crypto_settings
from tests.collector.test_supervisor import GUILD_STATE, USER_ID, FakeSession, Queue
from tests.db.builders import CHANNEL, seed_channel

Factory = async_sessionmaker[AsyncSession]
TOKEN = "fake-discord-token.abc123.XYZ"
KEYS = crypto_settings().key_ring
# AC-17 的硬限制：Compose 10 秒后强杀。
SHUTDOWN_LIMIT = 10.0


@pytest.fixture
def factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def states() -> list[GuildState]:
    """会话 READY 之后 FullSync 带的服务器，测试按需往里放。"""
    return []


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch, states: list[GuildState]) -> list[FakeSession]:
    """把真正连 Discord 的 new_client 换掉，其余都走真实流程。"""
    created: list[FakeSession] = []
    tokens: list[str] = []

    def new_client(
        generation: int, queue: Queue, _clock: object, guard: object = None
    ) -> FakeSession:
        assert isinstance(guard, WakeGuard)
        session = FakeSession(generation, queue, USER_ID, tokens, states)
        created.append(session)
        return session

    monkeypatch.setattr(main, "new_client", new_client)
    monkeypatch.setattr(main, "get_crypto_settings", crypto_settings)
    return created


async def add_account(factory: Factory) -> AccountRow:
    async with factory() as session, session.begin():
        row = await accounts.create_account(
            session, "main", encrypt(KEYS, PURPOSE_DISCORD_TOKEN, TOKEN.encode()), 1
        )
    assert row != "exists"
    return row


async def read_account(factory: Factory) -> AccountRow:
    async with factory() as session:
        row = await accounts.get_account(session)
    assert row is not None
    return row


async def wait_until(check: Callable[[], Awaitable[bool]], limit: float = 5.0) -> None:
    async with asyncio.timeout(limit):
        while not await check():  # noqa: ASYNC110  # 等的是数据库状态，没有可以等的 Event。
            await asyncio.sleep(0.01)


async def wait_online(factory: Factory, limit: float = 5.0) -> None:
    async def online() -> bool:
        async with factory() as session:
            row = await accounts.get_account(session)
        return row is not None and row.status == "online"

    await wait_until(online, limit)


async def terminate_lock_backend() -> None:
    conn = await raw.connect()
    try:
        terminated = await conn.fetchval(
            """
            SELECT pg_terminate_backend(pid) FROM pg_locks
            WHERE locktype = 'advisory' AND objid::bigint = $1
              AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
            """,
            raw.COLLECTOR_LOCK_KEY,
        )
    finally:
        await conn.close()
    assert terminated is True


async def stop_with_sigterm(task: "asyncio.Task[int]") -> int:
    os.kill(os.getpid(), signal.SIGTERM)
    return await asyncio.wait_for(task, SHUTDOWN_LIMIT)


async def test_idle_collector_connects_on_notify_without_restart(
    factory: Factory, sessions: list[FakeSession]
) -> None:  # covers: AC-1
    task = asyncio.create_task(main.run())
    await asyncio.sleep(0.2)
    assert sessions == []

    # 轮询要 30 秒才来一次，所以 5 秒内连上只可能是 NOTIFY 叫醒的。
    await add_account(factory)
    await wait_online(factory, limit=5.0)

    assert sessions[0].tokens == [TOKEN]
    assert await stop_with_sigterm(task) == main.EXIT_OK


async def test_lock_held_elsewhere_exits_without_logging_in(
    factory: Factory, sessions: list[FakeSession]
) -> None:  # covers: AC-14
    await add_account(factory)
    holder = await raw.connect()
    try:
        await holder.fetchval("SELECT pg_advisory_lock($1)", raw.COLLECTOR_LOCK_KEY)

        assert await asyncio.wait_for(main.run(), 5) == main.EXIT_LOCK_HELD
        assert sessions == []
    finally:
        await holder.close()

    # 另一个进程放开锁之后，重启才会登录。
    task = asyncio.create_task(main.run())
    await wait_online(factory)
    assert len(sessions) == 1
    assert await stop_with_sigterm(task) == main.EXIT_OK


async def test_losing_the_lock_connection_exits_immediately(
    factory: Factory, sessions: list[FakeSession]
) -> None:  # covers: AC-14
    await add_account(factory)
    task = asyncio.create_task(main.run())
    await wait_online(factory)

    await terminate_lock_backend()

    assert await asyncio.wait_for(task, 5) == main.EXIT_LOCK_LOST
    assert sessions[0].closed


async def test_sigterm_closes_session_and_coverage_without_status_change(
    factory: Factory, sessions: list[FakeSession], states: list[GuildState]
) -> None:  # covers: AC-17
    row = await add_account(factory)
    async with factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collector_account_id=row.id, collect_enabled=True)
        )
        await session.execute(
            insert(AccountChannelAccess).values(
                account_id=row.id, channel_id=CHANNEL.id, can_read=True
            )
        )
    states.append(GUILD_STATE)
    task = asyncio.create_task(main.run())
    await wait_online(factory)

    async def coverage_open() -> bool:
        async with factory() as session:
            count = await session.scalar(select(func.count()).select_from(ChannelCoverage))
        return bool(count)

    async with asyncio.timeout(5):
        while not await coverage_open():  # noqa: ASYNC110  # 等的是数据库状态。
            await asyncio.sleep(0.01)
    started = time.monotonic()

    assert await stop_with_sigterm(task) == main.EXIT_OK

    assert time.monotonic() - started < SHUTDOWN_LIMIT
    assert sessions[0].closed
    assert (await read_account(factory)).status == "online"
    async with factory() as session:
        open_spans = await session.scalar(
            select(text("count(*)")).select_from(ChannelCoverage).where(text("upper_inf(span)"))
        )
        total_spans = await session.scalar(select(text("count(*)")).select_from(ChannelCoverage))
    assert (open_spans, total_spans) == (0, 1)


async def test_sigterm_while_idle_exits_cleanly(
    engine: AsyncEngine, sessions: list[FakeSession]
) -> None:  # covers: AC-17
    task = asyncio.create_task(main.run())
    await asyncio.sleep(0.2)

    assert await stop_with_sigterm(task) == main.EXIT_OK
    assert sessions == []


async def test_unexpected_failure_exits_with_crash_code_and_releases_lock(
    factory: Factory, sessions: list[FakeSession], capfd: pytest.CaptureFixture[str]
) -> None:
    await add_account(factory)
    task = asyncio.create_task(main.run())
    await wait_online(factory)
    current = sessions[0]

    # 异常内容可能带敏感信息：进程只记类型，不记内容。
    current.queue.put_nowait(SessionEnded(current.generation, unexpected=RuntimeError(TOKEN)))

    assert await asyncio.wait_for(task, 5) == main.EXIT_CRASH
    captured = capfd.readouterr()
    assert TOKEN not in captured.out + captured.err
    assert current.closed
    holder = await raw.connect()
    try:
        assert await holder.fetchval("SELECT pg_try_advisory_lock($1)", raw.COLLECTOR_LOCK_KEY)
    finally:
        await holder.close()
