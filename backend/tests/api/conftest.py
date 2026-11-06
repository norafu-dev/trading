"""api 测试：真实 PostgreSQL，加注入的时钟；不跑 lifespan，所以没有 LISTEN 任务。"""

import base64
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pyotp
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.api import auth_core as core
from harvester.api.auth_store import issue_setup_code
from harvester.api.deps import get_clock
from harvester.api.main import create_app
from harvester.config import ApiSettings, CryptoSettings
from harvester.db import get_session_factory
from harvester.media.urls import LocalMediaDir

ORIGIN = "http://localhost:8080"
PASSWORD = "correct horse battery"
TEST_KEY = f"1:{base64.b64encode(bytes(range(32))).decode()}"
# 不看导出链接的测试用这个占位；看链接的测试用 `app` fixture 里的 tmp_path。
UNUSED_LINK_BASE = LocalMediaDir(Path("/nonexistent/harvester-media"))


@dataclass
class FakeClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def api_settings(secure: bool = False, origin: str = ORIGIN) -> ApiSettings:
    return ApiSettings(_env_file=None, site_origin=origin, session_cookie_secure=secure)  # pyright: ignore[reportCallIssue]


def crypto_settings() -> CryptoSettings:
    return CryptoSettings(_env_file=None, token_encryption_key=SecretStr(TEST_KEY))  # pyright: ignore[reportCallIssue]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def wire(app: FastAPI, factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> FastAPI:
    async def _factory() -> async_sessionmaker[AsyncSession]:
        return factory

    async def _clock() -> FakeClock:
        return clock

    app.dependency_overrides[get_session_factory] = _factory
    app.dependency_overrides[get_clock] = _clock
    return app


@pytest.fixture
def app(factory: async_sessionmaker[AsyncSession], clock: FakeClock, tmp_path: Path) -> FastAPI:
    app = create_app(api_settings(), crypto_settings(), media_link_base=LocalMediaDir(tmp_path))
    return wire(app, factory, clock)


def make_client(app: FastAPI, ip: str = "203.0.113.7") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 1234)),
        base_url="http://test",
        headers={"Origin": ORIGIN, "User-Agent": "pytest-agent"},
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as c:
        yield c


def code_for(secret: str, now: datetime, step_offset: int = 0) -> str:
    return pyotp.TOTP(secret).generate_otp(core.totp_step(now) + step_offset)


async def new_setup_code(factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> str:
    async with factory() as session, session.begin():
        code = await issue_setup_code(session, clock())
    assert code is not None
    return code


async def complete_setup(
    client: httpx.AsyncClient, factory: async_sessionmaker[AsyncSession], clock: FakeClock
) -> str:
    """走完整的设置流程，返回 TOTP 密钥。结束时 client 处于登录状态。"""
    setup_code = await new_setup_code(factory, clock)
    begin = await client.post("/api/auth/setup/begin", json={"setup_code": setup_code})
    assert begin.status_code == 200
    secret = begin.json()["secret"]
    done = await client.post(
        "/api/auth/setup/complete",
        json={
            "setup_code": setup_code,
            "password": PASSWORD,
            "totp_code": code_for(secret, clock()),
        },
    )
    assert done.status_code == 204
    # 设置用掉了当前时间步，往后走两步，后面的登录才有新验证码。
    clock.advance(seconds=60)
    return secret


async def do_login(
    client: httpx.AsyncClient, secret: str, clock: FakeClock, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        "/api/auth/login", json={"password": password, "totp_code": code_for(secret, clock())}
    )


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: async_sessionmaker[AsyncSession], clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client
