"""AC-5 到 AC-8、AC-10、AC-11、AC-13 接口部分：登录、会话、登出。"""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from harvester.api import auth_core as core
from harvester.api import routes
from harvester.api.deps import CurrentSession
from harvester.api.main import create_app
from harvester.api.notifications import Broadcaster
from harvester.db.models import DashboardCredential, DashboardSession, LoginEvent
from tests.api.conftest import (
    ORIGIN,
    PASSWORD,
    FakeClock,
    api_settings,
    code_for,
    complete_setup,
    crypto_settings,
    do_login,
    make_client,
    wire,
)

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def secret(client: httpx.AsyncClient, factory: Factory, clock: FakeClock) -> str:
    """已设置好，client 处于未登录状态。"""
    value = await complete_setup(client, factory, clock)
    client.cookies.clear()
    return value


async def last_step(factory: Factory) -> int | None:
    async with factory() as s:
        credential = await s.scalar(select(DashboardCredential))
    assert credential is not None
    return credential.totp_last_step


async def test_correct_login_opens_protected_routes(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-5
    assert (await client.get("/api/auth/state")).json() == {"state": "unauthenticated"}
    assert (await do_login(client, secret, clock)).status_code == 204
    assert (await client.get("/api/health")).status_code == 200
    assert (await client.get("/api/auth/state")).json() == {"state": "authenticated"}


async def test_wrong_password_and_wrong_code_look_identical(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-5
    bad_password = await do_login(client, secret, clock, password="wrong password here")
    bad_code = await client.post("/api/auth/login", json={"password": PASSWORD, "totp_code": "abc"})
    assert bad_password.status_code == bad_code.status_code == 401
    assert bad_password.json() == bad_code.json()
    assert bad_password.json()["error"]["code"] == "bad_credentials"


async def test_same_code_cannot_be_used_twice(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-5
    assert (await do_login(client, secret, clock)).status_code == 204
    assert (await do_login(client, secret, clock)).status_code == 401


@pytest.mark.parametrize("offset", [-1, 0, 1])
async def test_adjacent_steps_are_accepted(
    client: httpx.AsyncClient, secret: str, clock: FakeClock, offset: int
) -> None:  # covers: AC-5
    response = await client.post(
        "/api/auth/login",
        json={"password": PASSWORD, "totp_code": code_for(secret, clock(), offset)},
    )
    assert response.status_code == 204


async def test_two_steps_away_is_rejected(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-5
    response = await client.post(
        "/api/auth/login", json={"password": PASSWORD, "totp_code": code_for(secret, clock(), 2)}
    )
    assert response.status_code == 401


async def test_wrong_password_does_not_burn_the_code(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-5
    before = await last_step(factory)
    assert (
        await do_login(client, secret, clock, password="wrong password here")
    ).status_code == 401
    assert await last_step(factory) == before
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_cookie_attributes_local(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-6
    response = await do_login(client, secret, clock)
    header = response.headers["set-cookie"]
    value = header.split(";")[0].split("=", 1)[1]
    assert header.startswith("harvester_session=")
    lowered = header.lower()
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/" in lowered
    assert "max-age=2592000" in lowered
    assert "secure" not in lowered
    assert "domain" not in lowered
    assert len(value) >= 43
    async with factory() as s:
        hashes = list(await s.scalars(select(DashboardSession.token_hash)))
    assert core.hash_token(value) in hashes
    assert value.encode() not in hashes


async def test_cookie_attributes_production(
    factory: Factory, clock: FakeClock
) -> None:  # covers: AC-6
    origin = "https://dash.example.com"
    app = wire(
        create_app(api_settings(secure=True, origin=origin), crypto_settings()), factory, clock
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 1)),
        base_url=origin,
        headers={"Origin": origin},
    ) as c:
        secret = await complete_setup(c, factory, clock)
        header = (await do_login(c, secret, clock)).headers["set-cookie"]
    assert header.startswith("__Host-harvester_session=")
    assert "secure" in header.lower()
    assert "domain" not in header.lower()


def cookie_hash(response: httpx.Response) -> bytes:
    return core.hash_token(response.headers["set-cookie"].split(";")[0].split("=", 1)[1])


async def test_login_replaces_presented_old_session(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-6
    old = cookie_hash(await do_login(client, secret, clock))
    clock.advance(seconds=60)
    new = cookie_hash(await do_login(client, secret, clock))
    async with factory() as s:
        assert await s.get(DashboardSession, old) is None
        assert await s.get(DashboardSession, new) is not None


async def test_session_idle_expiry(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-7
    await do_login(client, secret, clock)
    clock.advance(days=6, hours=23)
    assert (await client.get("/api/health")).status_code == 200
    clock.advance(days=7)
    assert (await client.get("/api/health")).status_code == 401


async def test_session_absolute_expiry_even_when_used(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-7
    await do_login(client, secret, clock)
    for _ in range(4):
        clock.advance(days=6)
        assert (await client.get("/api/health")).status_code == 200
    clock.advance(days=6)
    assert (await client.get("/api/health")).status_code == 401


async def test_last_seen_written_at_most_every_60_seconds(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-7
    token_hash = cookie_hash(await do_login(client, secret, clock))
    start = clock()

    async def last_seen() -> datetime | None:
        async with factory() as s:
            return await s.scalar(
                select(DashboardSession.last_seen_at).where(
                    DashboardSession.token_hash == token_hash
                )
            )

    clock.advance(seconds=59)
    await client.get("/api/health")
    assert await last_seen() == start
    clock.advance(seconds=1)
    await client.get("/api/health")
    assert await last_seen() == start + timedelta(seconds=60)


async def test_logout_invalidates_the_same_cookie(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-8
    response = await do_login(client, secret, clock)
    value = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 204
    assert "harvester_session=" in logout.headers["set-cookie"]
    client.cookies.set("harvester_session", value)
    assert (await client.get("/api/health")).status_code == 401


async def test_logout_without_session_is_204_and_writes_nothing(
    client: httpx.AsyncClient, factory: Factory, secret: str
) -> None:  # covers: AC-8
    async with factory() as s:
        before = len(list(await s.scalars(select(LoginEvent.id))))
    assert (await client.post("/api/auth/logout")).status_code == 204
    async with factory() as s:
        assert len(list(await s.scalars(select(LoginEvent.id)))) == before


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://evil.example"}, {"Origin": "null"}, {"Origin": ORIGIN + "/"}]
)
async def test_bad_origin_is_403_and_not_logged(
    client: httpx.AsyncClient, factory: Factory, headers: dict[str, str]
) -> None:  # covers: AC-10
    for path in ("/api/auth/login", "/api/auth/setup/begin", "/api/auth/logout"):
        response = await client.post(path, json={}, headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "bad_origin")
    async with factory() as s:
        assert list(await s.scalars(select(LoginEvent.id))) == []


async def test_missing_origin_is_403(app: FastAPI) -> None:  # covers: AC-10
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        response = await c.post("/api/auth/login", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "bad_origin")


async def test_login_events_are_protected_ordered_and_validated(
    client: httpx.AsyncClient, secret: str, clock: FakeClock
) -> None:  # covers: AC-11
    assert (await client.get("/api/auth/login-events")).status_code == 401
    await do_login(client, secret, clock, password="wrong password here")
    clock.advance(seconds=60)
    await do_login(client, secret, clock)
    clock.advance(seconds=60)
    await client.post("/api/auth/logout")
    clock.advance(seconds=60)
    await do_login(client, secret, clock)

    rows = (await client.get("/api/auth/login-events")).json()
    assert [r["outcome"] for r in rows] == [
        "success",
        "logout",
        "success",
        "bad_credentials",
        "setup_completed",
    ]
    assert rows[0]["ip"] == "203.0.113.7"
    assert rows[0]["user_agent"] == "pytest-agent"
    assert (await client.get("/api/auth/login-events", params={"limit": 500})).status_code == 422
    assert len((await client.get("/api/auth/login-events", params={"limit": 2})).json()) == 2


async def test_successful_login_purges_old_rows(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-11
    async with factory() as s, s.begin():
        s.add(
            LoginEvent(
                occurred_at=clock() - timedelta(days=181),
                ip="198.51.100.1",
                user_agent=None,
                outcome="logout",
            )
        )
        s.add(
            DashboardSession(
                token_hash=core.hash_token("stale"),
                created_at=clock() - timedelta(days=31),
                last_seen_at=clock() - timedelta(days=8),
                ip="198.51.100.1",
                user_agent=None,
            )
        )
    assert (await do_login(client, secret, clock)).status_code == 204
    async with factory() as s:
        assert await s.get(DashboardSession, core.hash_token("stale")) is None
        ips = list(await s.scalars(select(LoginEvent.ip)))
    assert "198.51.100.1" not in [str(ip) for ip in ips]


async def test_user_agent_is_truncated(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-11
    response = await client.post(
        "/api/auth/login",
        json={"password": PASSWORD, "totp_code": code_for(secret, clock())},
        headers={"User-Agent": "x" * 2000},
    )
    async with factory() as s:
        ua = await s.scalar(
            select(DashboardSession.user_agent).where(
                DashboardSession.token_hash == cookie_hash(response)
            )
        )
    assert ua is not None
    assert len(ua) == 512


async def _stream(factory: Factory, clock: FakeClock, token_hash: bytes) -> AsyncIterator[object]:
    async for event in routes.events(Broadcaster(), CurrentSession(token_hash), factory, clock):  # pyright: ignore[reportGeneralTypeIssues]
        yield event


async def test_sse_stops_after_session_deleted(
    client: httpx.AsyncClient,
    factory: Factory,
    secret: str,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # covers: AC-14
    monkeypatch.setattr(routes, "HEARTBEAT_INTERVAL", 0.01)
    response = await do_login(client, secret, clock)
    token = response.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    token_hash = core.hash_token(token)
    stream = _stream(factory, clock, token_hash)

    assert await anext(stream) is not None
    assert await anext(stream) is not None  # 会话有效：第二次心跳照常发出。

    await client.post("/api/auth/logout")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=2)


async def test_database_down_is_503_not_401(clock: FakeClock) -> None:  # covers: AC-13
    dead = create_async_engine("postgresql+asyncpg://nobody:x@127.0.0.1:1/none")
    app = wire(
        create_app(api_settings(), crypto_settings()),
        async_sessionmaker(dead, expire_on_commit=False),
        clock,
    )
    async with make_client(app) as c:
        c.cookies.set("harvester_session", "whatever")
        for path in ("/api/health", "/api/auth/state"):
            response = await c.get(path)
            assert (response.status_code, response.json()["error"]["code"]) == (
                503,
                "database_unavailable",
            )
        assert (await c.get("/api/livez")).status_code == 200
    await dead.dispose()
