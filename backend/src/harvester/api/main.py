import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from harvester.api.auth_routes import public_router
from harvester.api.errors import register_error_handlers
from harvester.api.middleware import origin_check
from harvester.api.notifications import Broadcaster, listen_forever
from harvester.api.routes import router
from harvester.config import (
    ApiSettings,
    CryptoSettings,
    get_api_settings,
    get_crypto_settings,
    is_local_origin,
)
from harvester.db import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
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


def create_app(settings: ApiSettings, crypto: CryptoSettings) -> FastAPI:
    # 生产环境不提供文档路由。
    docs_enabled = is_local_origin(settings.site_origin)
    app = FastAPI(
        title="Harvester",
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.state.api_settings = settings
    app.state.key_ring = crypto.key_ring
    register_error_handlers(app)
    app.add_middleware(origin_check, site_origin=settings.site_origin)
    app.include_router(public_router)
    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn 的工厂入口；配置不全时启动就报错退出。"""
    return create_app(get_api_settings(), get_crypto_settings())
