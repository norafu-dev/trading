import asyncio
from collections.abc import AsyncIterable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from harvester.api.deps import (
    ClockDep,
    CurrentSessionDep,
    protected_router,
    session_still_valid,
)
from harvester.api.discord_routes import discord_router
from harvester.api.notifications import Broadcaster
from harvester.db import SessionDep, SessionFactoryDep

# 不管有没有事件，都按这个间隔发心跳并重新检查会话（AC-14）。
HEARTBEAT_INTERVAL = 20.0

router = protected_router
router.include_router(discord_router)


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
    """A heartbeat every 20 s plus new_message and account_status notifications."""
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
            if payload.get("type") == "account_status":
                # 事件本身不带状态，前端收到后重新取数。data 不能省：没有 data 的 SSE 事件
                # 浏览器不会派发。
                yield ServerSentEvent(data={}, event="account_status")
                continue
            message_id = payload.get("message_id")
            yield ServerSentEvent(
                data=payload,
                event="new_message",
                id=str(message_id) if message_id is not None else None,
            )
