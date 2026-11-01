"""AC-9、AC-11：限速只从 login_event 算。"""

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.models import LoginEvent
from tests.api.conftest import PASSWORD, FakeClock, complete_setup, do_login, make_client

Factory = async_sessionmaker[AsyncSession]
IP = "203.0.113.7"
OTHER_IP = "203.0.113.8"
BAD = "wrong password here"


@pytest.fixture
async def secret(client: httpx.AsyncClient, factory: Factory, clock: FakeClock) -> str:
    value = await complete_setup(client, factory, clock)
    client.cookies.clear()
    return value


async def seed(
    factory: Factory, clock: FakeClock, n: int, ip: str = IP, age_seconds: float = 0
) -> None:
    async with factory() as s, s.begin():
        s.add_all(
            LoginEvent(
                occurred_at=clock() - timedelta(seconds=age_seconds),
                ip=ip,
                user_agent=None,
                outcome="bad_credentials",
            )
            for _ in range(n)
        )


async def count_outcome(factory: Factory, outcome: str) -> int:
    async with factory() as s:
        return (
            await s.scalar(
                select(func.count()).select_from(LoginEvent).where(LoginEvent.outcome == outcome)
            )
            or 0
        )


async def test_sixth_attempt_waits_and_correct_password_is_not_checked(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    for _ in range(5):
        assert (await do_login(client, secret, clock, password=BAD)).status_code == 401
    blocked = await do_login(client, secret, clock)
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "60"
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.json()["error"]["retry_after_seconds"] == 60

    clock.advance(seconds=61)
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_backoff_doubles_after_another_failure(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    await seed(factory, clock, 5)
    clock.advance(seconds=61)
    assert (await do_login(client, secret, clock, password=BAD)).status_code == 401
    blocked = await do_login(client, secret, clock)
    assert blocked.json()["error"]["retry_after_seconds"] == 120


async def test_success_resets_the_count(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    await seed(factory, clock, 4)
    assert (await do_login(client, secret, clock)).status_code == 204
    clock.advance(seconds=60)
    await seed(factory, clock, 4)
    client.cookies.clear()
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_failures_older_than_24h_do_not_count(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    await seed(factory, clock, 20, age_seconds=25 * 3600)
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_other_ip_success_does_not_help(
    app: FastAPI, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    await seed(factory, clock, 5, ip=OTHER_IP)
    async with make_client(app) as me, make_client(app, ip=OTHER_IP) as attacker:
        assert (await do_login(me, secret, clock)).status_code == 204
        assert (await do_login(attacker, secret, clock)).status_code == 429


async def test_rate_limited_events_do_not_count_or_interrupt(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9, AC-11
    await seed(factory, clock, 5)
    for _ in range(20):
        assert (await do_login(client, secret, clock, password=BAD)).status_code == 429
    assert await count_outcome(factory, "rate_limited") == 1
    assert await count_outcome(factory, "bad_credentials") == 5

    # 60 秒之后再被挡，才会多记一条；等待时间没有因此变长。
    clock.advance(seconds=59)
    blocked = await do_login(client, secret, clock, password=BAD)
    assert blocked.json()["error"]["retry_after_seconds"] == 1
    assert await count_outcome(factory, "rate_limited") == 1


async def test_setup_code_failures_share_the_counter(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-9
    for _ in range(5):
        response = await client.post("/api/auth/setup/begin", json={"setup_code": "bad"})
        assert response.status_code == 403
    response = await client.post("/api/auth/setup/begin", json={"setup_code": "bad"})
    assert response.status_code == 429
    response = await client.post(
        "/api/auth/setup/complete",
        json={"setup_code": "bad", "password": PASSWORD, "totp_code": "000000"},
    )
    assert response.status_code == 429


async def test_global_limit_blocks_clean_ip_above_30(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    # 分散在很多 IP 上，每个都没到自己的门槛。
    for i in range(30):
        await seed(factory, clock, 1, ip=f"198.51.100.{i + 1}")
    client.cookies.clear()
    assert (await do_login(client, secret, clock, password=BAD)).status_code == 401
    # 现在正好 31 次，超过 30。
    blocked = await do_login(client, secret, clock)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["retry_after_seconds"] == 60

    clock.advance(seconds=61)
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_successes_do_not_count_towards_global_limit(
    client: httpx.AsyncClient, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    async with factory() as s, s.begin():
        s.add_all(
            LoginEvent(occurred_at=clock(), ip=OTHER_IP, user_agent=None, outcome="success")
            for _ in range(40)
        )
    assert (await do_login(client, secret, clock)).status_code == 204


async def test_concurrent_bad_logins_stop_at_five(
    app: FastAPI, factory: Factory, secret: str, clock: FakeClock
) -> None:  # covers: AC-9
    async with make_client(app) as c:
        results = await asyncio.gather(
            *(do_login(c, secret, clock, password=BAD) for _ in range(10))
        )
    codes = sorted(r.status_code for r in results)
    assert codes == [401] * 5 + [429] * 5
    assert await count_outcome(factory, "bad_credentials") == 5
