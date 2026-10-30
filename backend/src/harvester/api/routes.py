import asyncio
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from harvester.api.notifications import Broadcaster
from harvester.db import SessionDep

HEARTBEAT_INTERVAL = 20.0

router = APIRouter(prefix="/api")


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
async def events(broadcaster: BroadcasterDep) -> AsyncIterable[ServerSentEvent]:
    """Demo stream: a heartbeat every 20 s plus any new_message notifications."""
    async with broadcaster.subscribe() as queue:
        yield ServerSentEvent(data=Heartbeat(server_time=datetime.now(UTC)), event="heartbeat")
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL)
            except TimeoutError:
                yield ServerSentEvent(
                    data=Heartbeat(server_time=datetime.now(UTC)), event="heartbeat"
                )
                continue
            message_id = payload.get("message_id")
            yield ServerSentEvent(
                data=payload,
                event="new_message",
                id=str(message_id) if message_id is not None else None,
            )
