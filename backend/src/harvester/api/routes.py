import asyncio
from collections.abc import AsyncIterable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from harvester.api.channel_routes import channel_router
from harvester.api.deps import (
    ClockDep,
    CurrentSessionDep,
    protected_router,
    session_still_valid,
)
from harvester.api.discord_routes import discord_router
from harvester.api.message_routes import message_router
from harvester.api.notifications import Broadcaster
from harvester.api.system_routes import system_router
from harvester.db import SessionDep, SessionFactoryDep

# 不管有没有事件，都按这个间隔发心跳并重新检查会话（AC-14）。
HEARTBEAT_INTERVAL = 20.0

# 只通知“数据变了”的事件：不带载荷，前端收到后重新取数。
_REFETCH_EVENTS = frozenset({"account_status", "channels_changed"})

router = protected_router
router.include_router(discord_router)
router.include_router(channel_router)
router.include_router(message_router)
router.include_router(system_router)


def get_broadcaster(request: Request) -> Broadcaster:
    return request.app.state.broadcaster


BroadcasterDep = Annotated[Broadcaster, Depends(get_broadcaster)]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unreachable"]
    database_time: datetime | None = None


class Heartbeat(BaseModel):
    server_time: datetime


@router.get("/health", tags=["system"])
async def health(session: SessionDep, response: Response) -> Health:
    try:
        now = await session.scalar(text("SELECT now()"))
    except (SQLAlchemyError, OSError):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database="unreachable")
    return Health(status="ok", database="ok", database_time=now)


@router.get("/events", response_class=EventSourceResponse, tags=["system"])
async def events(
    broadcaster: BroadcasterDep,
    current: CurrentSessionDep,
    factory: SessionFactoryDep,
    clock: ClockDep,
) -> AsyncIterable[ServerSentEvent]:
    """A heartbeat every 20 s plus message, account and channel change events."""
    loop = asyncio.get_running_loop()
    async with broadcaster.subscribe() as queue:
        yield ServerSentEvent(data=Heartbeat(server_time=clock()), event="heartbeat")
        next_check = loop.time() + HEARTBEAT_INTERVAL
        while True:
            remaining = next_check - loop.time()
            if remaining <= 0:
                try:
                    still_valid = await session_still_valid(factory, current.token_hash, clock)
                except (SQLAlchemyError, OSError):
                    still_valid = False
                if not still_valid:
                    return
                yield ServerSentEvent(data=Heartbeat(server_time=clock()), event="heartbeat")
                next_check = loop.time() + HEARTBEAT_INTERVAL
                continue
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                continue
            if payload.get("type") in _REFETCH_EVENTS:
                # 事件本身不带状态，前端收到后重新取数。data 不能省：没有 data 的 SSE 事件
                # 浏览器不会派发。
                yield ServerSentEvent(data={}, event=payload["type"])
                continue
            # 消息事件只带 ID，页面收到后从消息接口取数。不设 SSE 的 id：断线靠前端重新取数补上。
            yield ServerSentEvent(data=payload, event=payload["type"])
