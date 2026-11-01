"""NOTIFY 到 SSE 订阅者这一段：真实 PostgreSQL 的 LISTEN（AC-15）。"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.api.notifications import Broadcaster, listen_forever
from harvester.db import account as accounts
from harvester.db import raw
from harvester.db.account import AccountRow

Factory = async_sessionmaker[AsyncSession]
Events = asyncio.Queue[dict[str, Any]]
# 探针的 account_id：真实账号的 id 从 1 开始，所以不会撞上。
PROBE_ID = 0
PROBE = {"type": "account_status", "account_id": PROBE_ID}
CIPHERTEXT = b"cipher-bytes"


@pytest.fixture
def factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


async def send_raw(factory: Factory, channel: str, payload: str) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            text("SELECT pg_notify(:channel, :payload)"), {"channel": channel, "payload": payload}
        )


async def send_probe(factory: Factory) -> None:
    await send_raw(factory, raw.ACCOUNT_CHANGED_CHANNEL, f'{{"account_id": {PROBE_ID}}}')


@pytest.fixture
async def events(factory: Factory) -> AsyncIterator[Events]:
    """开始监听，并等到两个通道都真正 LISTEN 上（账号通道是最后一个）。"""
    broadcaster = Broadcaster()
    async with broadcaster.subscribe() as queue:
        task = asyncio.create_task(listen_forever(broadcaster))
        async with asyncio.timeout(5):
            while queue.empty():  # 只能反复发探针，LISTEN 就绪没有信号。
                await send_probe(factory)
                await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)
        while not queue.empty():
            queue.get_nowait()
        yield queue
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def next_event(queue: Events) -> dict[str, Any]:
    return await asyncio.wait_for(queue.get(), 5)


async def assert_only_probe_arrives(factory: Factory, queue: Events) -> None:
    """通知按提交顺序到达：探针先到，就说明它前面没有别的通知。"""
    await send_probe(factory)
    assert await next_event(queue) == PROBE


async def add_account(factory: Factory) -> AccountRow:
    async with factory() as session, session.begin():
        row = await accounts.create_account(session, "main", CIPHERTEXT, 1)
    assert row != "exists"
    return row


async def test_committed_account_write_reaches_subscribers(
    factory: Factory, events: Events
) -> None:  # covers: AC-15
    row = await add_account(factory)

    event = await next_event(events)

    # 载荷只有 id，token 和其他字段都不走通知。
    assert event == {"type": "account_status", "account_id": row.id}


async def test_new_message_channel_keeps_its_own_event_type(
    factory: Factory, events: Events
) -> None:
    await send_raw(factory, raw.NEW_MESSAGE_CHANNEL, '{"message_id": "9"}')

    assert await next_event(events) == {"type": "new_message", "message_id": "9"}


async def test_rolled_back_write_sends_nothing(factory: Factory, events: Events) -> None:
    async with factory() as session, session.begin():
        await accounts.create_account(session, "main", CIPHERTEXT, 1)
        await session.rollback()

    await assert_only_probe_arrives(factory, events)


async def test_malformed_payload_is_dropped_and_listening_continues(
    factory: Factory, events: Events
) -> None:
    await send_raw(factory, raw.ACCOUNT_CHANGED_CHANNEL, "not json")

    await assert_only_probe_arrives(factory, events)


Write = Callable[[AsyncSession], Awaitable[object]]


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(lambda s: accounts.update_label(s, "renamed"), id="label"),
        pytest.param(lambda s: accounts.replace_token(s, CIPHERTEXT, 1), id="token"),
        pytest.param(lambda s: accounts.set_enabled(s, False), id="disable"),
    ],
)
async def test_every_api_write_notifies(
    factory: Factory, events: Events, write: Write
) -> None:  # covers: AC-15
    row = await add_account(factory)
    await next_event(events)

    async with factory() as session, session.begin():
        await write(session)

    assert await next_event(events) == {"type": "account_status", "account_id": row.id}


async def test_status_write_notifies_but_heartbeat_does_not(
    factory: Factory, events: Events
) -> None:  # covers: AC-15
    row = await add_account(factory)
    await next_event(events)

    async with factory() as session, session.begin():
        await accounts.record_status(session, row.id, 1, "online", None)
    assert (await next_event(events))["account_id"] == row.id

    async with factory() as session, session.begin():
        await accounts.record_heartbeat(session, row.id, 1)
    await assert_only_probe_arrives(factory, events)


async def test_disabling_twice_notifies_only_once(factory: Factory, events: Events) -> None:
    await add_account(factory)
    await next_event(events)
    async with factory() as session, session.begin():
        await accounts.set_enabled(session, False)
    await next_event(events)

    async with factory() as session, session.begin():
        await accounts.set_enabled(session, False)

    await assert_only_probe_arrives(factory, events)
