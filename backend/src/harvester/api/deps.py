"""api 的依赖：时钟、配置、当前请求信息和会话校验。

受保护的路由都挂在 `protected_router` 上，新接口默认要登录；公开接口只有 `PUBLIC_API_PATHS`。
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from harvester.api import auth_core as core
from harvester.api import auth_store as store
from harvester.api.errors import UnauthenticatedError
from harvester.config import ApiSettings
from harvester.crypto import KeyRing
from harvester.db import SessionFactoryDep

Clock = Callable[[], datetime]

# AC-1 和 AC-2 的白名单，开发环境的文档路由也在里面。改动它要有明确的理由。
PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth/state",
        "/api/auth/login",
        "/api/auth/setup/begin",
        "/api/auth/setup/complete",
        "/api/auth/logout",
        "/api/livez",
        "/api/docs",
        "/api/openapi.json",
    }
)


def _system_clock() -> datetime:
    return datetime.now(UTC)


async def get_clock() -> Clock:
    return _system_clock


ClockDep = Annotated[Clock, Depends(get_clock)]


def get_api_settings_dep(connection: HTTPConnection) -> ApiSettings:
    return connection.app.state.api_settings


ApiSettingsDep = Annotated[ApiSettings, Depends(get_api_settings_dep)]


def get_key_ring(connection: HTTPConnection) -> KeyRing:
    return connection.app.state.key_ring


KeyRingDep = Annotated[KeyRing, Depends(get_key_ring)]


def get_request_context(connection: HTTPConnection) -> core.RequestContext:
    client = connection.client
    return core.request_context(
        client.host if client is not None else None, connection.headers.get("user-agent")
    )


RequestContextDep = Annotated[core.RequestContext, Depends(get_request_context)]


def get_cookie_token_hash(connection: HTTPConnection, settings: ApiSettingsDep) -> bytes | None:
    raw = connection.cookies.get(core.session_cookie_name(settings.session_cookie_secure))
    return core.hash_token(raw) if raw else None


CookieTokenHashDep = Annotated[bytes | None, Depends(get_cookie_token_hash)]


@dataclass(frozen=True)
class CurrentSession:
    token_hash: bytes


async def session_row_is_valid(session: AsyncSession, token_hash: bytes, now: datetime) -> bool:
    row = await store.get_session_row(session, token_hash)
    return row is not None and core.session_is_valid(row.created_at, row.last_seen_at, now)


async def session_still_valid(
    factory: async_sessionmaker[AsyncSession], token_hash: bytes, clock: Clock
) -> bool:
    """单独开一个短的数据库会话，SSE 这种长请求不会一直占着连接。"""
    async with factory() as session:
        return await session_row_is_valid(session, token_hash, clock())


async def require_session(
    token_hash: CookieTokenHashDep, factory: SessionFactoryDep, clock: ClockDep
) -> CurrentSession:
    if token_hash is None:
        raise UnauthenticatedError
    now = clock()
    async with factory() as session, session.begin():
        row = await store.get_session_row(session, token_hash)
        if row is None or not core.session_is_valid(row.created_at, row.last_seen_at, now):
            raise UnauthenticatedError
        if core.session_needs_touch(row.last_seen_at, now):
            await store.touch_session(session, token_hash, now)
    return CurrentSession(token_hash)


CurrentSessionDep = Annotated[CurrentSession, Depends(require_session)]

protected_router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])
