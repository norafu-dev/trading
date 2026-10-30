import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from harvester.api.notifications import Broadcaster, listen_forever
from harvester.api.routes import router
from harvester.db import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    broadcaster = Broadcaster()
    app.state.broadcaster = broadcaster
    listener = asyncio.create_task(listen_forever(broadcaster), name="pg-listen")
    try:
        yield
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
        await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Harvester",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.include_router(router)
    return app


app = create_app()
