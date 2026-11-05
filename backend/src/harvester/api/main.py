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
from harvester.api.search_backfill import run_search_backfill
from harvester.config import (
    ApiSettings,
    CryptoSettings,
    LocalMediaSettings,
    get_api_settings,
    get_crypto_settings,
    is_local_origin,
    load_media_settings,
)
from harvester.db import get_engine, get_sessionmaker
from harvester.media.storage import MediaStore, R2Store, build_local_store, build_r2_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    broadcaster = Broadcaster()
    app.state.broadcaster = broadcaster
    listener = asyncio.create_task(listen_forever(broadcaster), name="pg-listen")
    backfill = asyncio.create_task(run_search_backfill(get_sessionmaker()), name="search-backfill")
    try:
        yield
    finally:
        for task in (listener, backfill):
            task.cancel()
        for task in (listener, backfill):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await get_engine().dispose()


def create_app(
    settings: ApiSettings, crypto: CryptoSettings, media_store: MediaStore | None = None
) -> FastAPI:
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
    app.state.media_store = media_store
    register_error_handlers(app)
    app.add_middleware(origin_check, site_origin=settings.site_origin)
    app.include_router(public_router)
    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn 的工厂入口；配置不全时启动就报错退出。"""
    return create_app(get_api_settings(), get_crypto_settings(), _build_media_store())


def _build_media_store() -> MediaStore:
    """MEDIA_STORAGE 没设置或 R2 配置不全时，这里抛校验错误，api 启动失败（AC-20）。"""
    settings = load_media_settings("api")
    if isinstance(settings, LocalMediaSettings):
        return build_local_store(settings.media_local_dir)
    client = build_r2_client(
        settings.endpoint_url,
        settings.media_r2_read_access_key_id.get_secret_value(),
        settings.media_r2_read_secret_access_key.get_secret_value(),
    )
    return R2Store(client=client, bucket=settings.media_r2_bucket)
