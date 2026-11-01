"""AC-3、AC-4、AC-12：设置和重置。"""

import asyncio

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.api.auth_store import issue_setup_code, reset_auth
from harvester.db.models import (
    DashboardCredential,
    DashboardSession,
    DashboardSetupCode,
    LoginEvent,
)
from tests.api.conftest import (
    PASSWORD,
    FakeClock,
    code_for,
    complete_setup,
    do_login,
    make_client,
    new_setup_code,
)

Factory = async_sessionmaker[AsyncSession]


async def begin_secret(client: httpx.AsyncClient, code: str) -> str:
    response = await client.post("/api/auth/setup/begin", json={"setup_code": code})
    return response.json()["secret"]


async def count(factory: Factory, model: type) -> int:
    async with factory() as s:
        return await s.scalar(select(func.count()).select_from(model)) or 0


async def outcomes(factory: Factory) -> list[str]:
    async with factory() as s:
        return list(await s.scalars(select(LoginEvent.outcome).order_by(LoginEvent.id)))


async def test_login_before_setup_is_409(client: httpx.AsyncClient) -> None:  # covers: AC-3
    state = await client.get("/api/auth/state")
    assert state.json() == {"state": "setup_required"}
    response = await client.post("/api/auth/login", json={"password": "x", "totp_code": "1"})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "setup_required")


async def test_no_setup_code_means_403(
    client: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-3
    response = await client.post("/api/auth/setup/begin", json={"setup_code": "anything"})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "setup_code_invalid")
    assert await outcomes(factory) == ["setup_bad_code"]


async def test_setup_code_stored_hashed_and_reusable(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-3
    code = await new_setup_code(factory, clock)
    async with factory() as s:
        row = await s.scalar(select(DashboardSetupCode))
    assert row is not None
    assert code.encode() not in row.code_hash
    for _ in range(2):
        response = await client.post("/api/auth/setup/begin", json={"setup_code": code})
        assert response.status_code == 200
        assert response.json()["otpauth_uri"].startswith("otpauth://totp/Harvester")


async def test_expired_setup_code_is_rejected(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-3
    code = await new_setup_code(factory, clock)
    clock.advance(hours=1, seconds=1)
    response = await client.post("/api/auth/setup/begin", json={"setup_code": code})
    assert response.status_code == 403


async def test_setup_code_command_refuses_once_set_up(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-3
    await complete_setup(client, factory, clock)
    async with factory() as s, s.begin():
        assert await issue_setup_code(s, clock()) is None


async def test_wrong_totp_saves_nothing_then_retry_works(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-4
    code = await new_setup_code(factory, clock)
    first = await begin_secret(client, code)
    bad = await client.post(
        "/api/auth/setup/complete",
        json={"setup_code": code, "password": PASSWORD, "totp_code": "000000"},
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "totp_invalid")
    assert await count(factory, DashboardCredential) == 0

    # 重新开始会生成新密钥，只有最后一次显示的有效。
    second = (await client.post("/api/auth/setup/begin", json={"setup_code": code})).json()[
        "secret"
    ]
    assert second != first
    good = await client.post(
        "/api/auth/setup/complete",
        json={"setup_code": code, "password": PASSWORD, "totp_code": code_for(second, clock())},
    )
    assert good.status_code == 204


async def test_wrong_totp_counts_towards_rate_limit(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-4, AC-9
    code = await new_setup_code(factory, clock)
    await begin_secret(client, code)
    body = {"setup_code": code, "password": PASSWORD, "totp_code": "000000"}
    for _ in range(5):
        bad = await client.post("/api/auth/setup/complete", json=body)
        assert bad.json()["error"]["code"] == "totp_invalid"

    limited = await client.post("/api/auth/setup/complete", json=body)
    assert (limited.status_code, limited.json()["error"]["code"]) == (429, "rate_limited")
    assert await outcomes(factory) == ["setup_bad_code"] * 5 + ["rate_limited"]


async def test_complete_saves_credential_and_logs_in(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-3, AC-4
    secret = await complete_setup(client, factory, clock)

    assert (await client.get("/api/auth/state")).json() == {"state": "authenticated"}
    assert await count(factory, DashboardSetupCode) == 0
    assert await outcomes(factory) == ["setup_completed"]
    async with factory() as s:
        credential = await s.scalar(select(DashboardCredential))
    assert credential is not None
    assert credential.password_hash.startswith("$argon2id$")
    assert secret.encode() not in credential.totp_secret_enc
    assert credential.totp_last_step is not None

    again = await client.post("/api/auth/setup/begin", json={"setup_code": "x"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_set_up")


async def test_password_length_counts_code_points_after_nfkc(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-4
    code = await new_setup_code(factory, clock)
    secret = (await client.post("/api/auth/setup/begin", json={"setup_code": code})).json()[
        "secret"
    ]
    body = {"setup_code": code, "totp_code": code_for(secret, clock())}
    assert (
        await client.post("/api/auth/setup/complete", json={**body, "password": "a" * 11})
    ).status_code == 422
    assert (
        await client.post("/api/auth/setup/complete", json={**body, "password": "a" * 129})
    ).status_code == 422
    # 全角 ａ 规范化成半角 a，登录时用半角也能对上。
    assert (
        await client.post("/api/auth/setup/complete", json={**body, "password": "ａ" * 12})
    ).status_code == 204
    clock.advance(seconds=60)
    assert (await do_login(client, secret, clock, password="a" * 12)).status_code == 204


async def test_two_concurrent_completes_save_one_credential(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-4
    code = await new_setup_code(factory, clock)
    async with make_client(app) as a, make_client(app) as b:
        secret = await begin_secret(a, code)
        body = {"setup_code": code, "password": PASSWORD, "totp_code": code_for(secret, clock())}
        results = await asyncio.gather(
            a.post("/api/auth/setup/complete", json=body),
            b.post("/api/auth/setup/complete", json=body),
        )
    assert sorted(r.status_code for r in results) == [204, 409]
    assert await count(factory, DashboardCredential) == 1


async def test_reset_returns_to_setup_required(
    client: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # covers: AC-12
    await complete_setup(client, factory, clock)
    async with factory() as s, s.begin():
        await reset_auth(s, clock())

    assert (await client.get("/api/auth/state")).json() == {"state": "setup_required"}
    assert (await client.get("/api/health")).status_code == 401
    assert await count(factory, DashboardSession) == 0
    async with factory() as s:
        event = await s.scalar(select(LoginEvent).order_by(LoginEvent.id.desc()))
    assert event is not None
    assert (event.outcome, event.ip) == ("reset", None)
