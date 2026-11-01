"""AC-17：日志和响应里没有密码、验证码、设置码、TOTP 密钥和 cookie 值。"""

import logging
from collections.abc import Iterator

import httpx
import pytest
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.logging import configure_logging
from tests.api.conftest import PASSWORD, FakeClock, code_for, new_setup_code


@pytest.fixture
def restore_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers = []


async def test_full_flow_never_logs_secrets(
    client: httpx.AsyncClient,
    factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
) -> None:  # covers: AC-17
    configure_logging("DEBUG")
    setup_code = await new_setup_code(factory, clock)
    secret = (await client.post("/api/auth/setup/begin", json={"setup_code": setup_code})).json()[
        "secret"
    ]
    setup_totp = code_for(secret, clock())
    done = await client.post(
        "/api/auth/setup/complete",
        json={"setup_code": setup_code, "password": PASSWORD, "totp_code": setup_totp},
    )
    clock.advance(seconds=60)
    await client.post("/api/auth/login", json={"password": "wrong password here", "totp_code": "1"})
    login_totp = code_for(secret, clock())
    login = await client.post(
        "/api/auth/login", json={"password": PASSWORD, "totp_code": login_totp}
    )
    assert login.status_code == 204
    cookies = [r.headers["set-cookie"].split(";")[0].split("=", 1)[1] for r in (done, login)]
    await client.post("/api/auth/logout")

    out = capsys.readouterr()
    logged = out.out + out.err
    assert "auth.login_succeeded" in logged
    for value in (
        PASSWORD,
        "wrong password here",
        setup_code,
        secret,
        setup_totp,
        login_totp,
        *cookies,
    ):
        assert value not in logged


async def test_validation_error_does_not_echo_input(
    client: httpx.AsyncClient,
) -> None:  # covers: AC-17
    short = "short-pass"
    response = await client.post(
        "/api/auth/setup/complete",
        json={"setup_code": "the-setup-code", "password": short, "totp_code": "123456"},
    )
    assert response.status_code == 422
    assert short not in response.text
    assert "the-setup-code" not in response.text
    body = response.json()["error"]
    assert body["code"] == "validation_error"
    assert all(set(f) == {"loc", "type"} for f in body["fields"])
