"""LISTEN on PostgreSQL and fan notifications out to SSE subscribers in this process.

The api runs a single worker, so an in memory fan out is enough. Notifications lost
while the LISTEN connection is down are recovered by clients via Last-Event-ID.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog

from harvester.db import raw

log = structlog.get_logger("harvester.api.notifications")

# 每个通道对应的 SSE 事件名，放进载荷的 type 字段。
_EVENT_TYPES = {
    raw.NEW_MESSAGE_CHANNEL: "new_message",
    raw.ACCOUNT_CHANGED_CHANNEL: "account_status",
}

RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0
SUBSCRIBER_QUEUE_SIZE = 256


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, payload: dict[str, Any]) -> None:
        for queue in self._subscribers:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A stalled client; it catches up from the database on reconnect.
                log.warning("notifications.subscriber_full")

    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


async def _listen_until_closed(broadcaster: Broadcaster) -> None:
    """Open one LISTEN connection and return once PostgreSQL closes it."""

    def on_notify(_conn: object, _pid: int, channel: str, payload: object) -> None:
        if not isinstance(payload, str):
            log.warning("notifications.bad_payload")
            return
        try:
            broadcaster.publish({"type": _EVENT_TYPES[channel], **json.loads(payload)})
        except json.JSONDecodeError:
            log.warning("notifications.bad_payload")

    conn = await raw.connect()
    try:
        closed = asyncio.Event()
        conn.add_termination_listener(lambda _c: closed.set())
        for channel in _EVENT_TYPES:
            await conn.add_listener(channel, on_notify)
        log.info("notifications.listening", channels=list(_EVENT_TYPES))
        await closed.wait()
    finally:
        if not conn.is_closed():
            await conn.close()


async def listen_forever(broadcaster: Broadcaster) -> None:
    """Hold a dedicated LISTEN connection, reconnecting with backoff until cancelled."""
    delay = RECONNECT_MIN
    while True:
        started = asyncio.get_running_loop().time()
        try:
            await _listen_until_closed(broadcaster)
            log.warning("notifications.connection_lost")
        except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
            log.warning("notifications.connect_failed", error=str(exc), retry_in=delay)
        # A connection that stayed up for a while resets the backoff.
        if asyncio.get_running_loop().time() - started > RECONNECT_MAX:
            delay = RECONNECT_MIN
        await asyncio.sleep(delay)
        delay = min(delay * 2, RECONNECT_MAX)
