"""AC-3、AC-4、AC-16：账号接口（真实 PostgreSQL）。"""

import logging
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.api import routes
from harvester.api.deps import CurrentSession
from harvester.api.notifications import Broadcaster
from harvester.crypto import PURPOSE_DISCORD_TOKEN, PURPOSE_TOTP, DecryptError, decrypt
from harvester.db import account as accounts
from harvester.db.models import DiscordAccount
from harvester.logging import configure_logging
from tests.api.conftest import (
    FakeClock,
    complete_setup,
    crypto_settings,
    do_login,
    make_client,
)

Factory = async_sessionmaker[AsyncSession]
TOKEN = "fake-discord-token.abc123.XYZ_0987654321"
BASE = "/api/discord/account"


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


@pytest.fixture
def restore_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    logging.getLogger().handlers = []


async def _add(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(BASE, json={"label": " main ", "token": f" {TOKEN} "})


async def test_get_is_null_without_account(logged_in: httpx.AsyncClient) -> None:
    assert (await logged_in.get(BASE)).json() == {"account": None}


async def test_add_encrypts_and_never_returns_token(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-1, AC-4
    response = await _add(logged_in)

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "main"
    assert body["status"] == "connecting"
    assert body["collector_stale"] is False
    assert not any("token" in key for key in body)
    async with factory() as session:
        stored = await session.scalar(select(DiscordAccount.token_ciphertext))
    assert stored is not None and TOKEN.encode() not in stored
    keys = crypto_settings().key_ring
    assert decrypt(keys, PURPOSE_DISCORD_TOKEN, stored) == TOKEN.encode()
    # 密文绑定用途，不能和 TOTP 密文互换。
    assert isinstance(decrypt(keys, PURPOSE_TOTP, stored), DecryptError)


async def test_second_add_returns_409(logged_in: httpx.AsyncClient) -> None:  # covers: AC-3
    await _add(logged_in)
    response = await _add(logged_in)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_exists"


@pytest.mark.parametrize("bad", ["short", f"Bot {TOKEN}", "é" * 30, "x" * 513])
async def test_bad_token_format_is_rejected_without_echo(
    logged_in: httpx.AsyncClient, bad: str
) -> None:
    response = await logged_in.post(BASE, json={"label": "main", "token": bad})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "token_format_invalid"
    assert bad not in response.text


async def test_blank_label_is_rejected(logged_in: httpx.AsyncClient) -> None:
    response = await logged_in.post(BASE, json={"label": "   ", "token": TOKEN})
    assert response.status_code == 422


async def test_operations_follow_state_rules(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-11, AC-12
    for path in ("disable", "enable", "reconnect", "switch-identity"):
        assert (await logged_in.post(f"{BASE}/{path}")).status_code == 404
    assert (await logged_in.put(f"{BASE}/token", json={"token": TOKEN})).status_code == 404
    await _add(logged_in)

    assert (await logged_in.post(f"{BASE}/enable")).status_code == 409
    assert (await logged_in.post(f"{BASE}/reconnect")).status_code == 409
    assert (await logged_in.post(f"{BASE}/switch-identity")).status_code == 409

    assert (await logged_in.post(f"{BASE}/disable")).json()["status"] == "disabled"
    assert (await logged_in.post(f"{BASE}/enable")).json()["status"] == "connecting"

    async with factory() as session, session.begin():
        account = await accounts.get_account(session)
        assert account is not None
        await accounts.record_status(
            session, account.id, account.config_version, "token_invalid", "http_401"
        )
    reconnected = await logged_in.post(f"{BASE}/reconnect")
    assert reconnected.json()["status"] == "connecting"
    assert reconnected.json()["last_error"] is None


async def test_label_update_does_not_bump_version(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await _add(logged_in)
    async with factory() as session:
        before = await accounts.get_account(session)

    response = await logged_in.put(f"{BASE}/label", json={"label": "renamed"})

    async with factory() as session:
        after = await accounts.get_account(session)
    assert response.json()["label"] == "renamed"
    assert before is not None and after is not None
    assert before.config_version == after.config_version


async def test_replace_token_while_login_paused_clears_retry(
    logged_in: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:
    await _add(logged_in)
    async with factory() as session, session.begin():
        account = await accounts.get_account(session)
        assert account is not None
        await accounts.record_status(
            session, account.id, 1, "login_paused", "login_budget_exhausted", clock()
        )

    body = (await logged_in.put(f"{BASE}/token", json={"token": TOKEN})).json()

    assert (body["status"], body["retry_at"]) == ("connecting", None)


async def test_every_route_requires_login(client: httpx.AsyncClient) -> None:  # covers: AC-16
    routes = [
        ("GET", BASE),
        ("POST", BASE),
        ("PUT", f"{BASE}/label"),
        ("PUT", f"{BASE}/token"),
        ("POST", f"{BASE}/disable"),
        ("POST", f"{BASE}/enable"),
        ("POST", f"{BASE}/reconnect"),
        ("POST", f"{BASE}/switch-identity"),
    ]
    for method, path in routes:
        response = await client.request(method, path, json={})
        assert response.status_code == 401, path


async def test_bad_origin_is_rejected(logged_in: httpx.AsyncClient) -> None:  # covers: AC-16
    response = await logged_in.post(f"{BASE}/disable", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


async def test_token_never_reaches_logs_or_responses(
    logged_in: httpx.AsyncClient,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
) -> None:  # covers: AC-4
    configure_logging("DEBUG")
    responses = [
        await _add(logged_in),
        await logged_in.put(f"{BASE}/token", json={"token": TOKEN}),
        await logged_in.post(BASE, json={"label": "x" * 200, "token": TOKEN}),
        await logged_in.get(BASE),
    ]
    assert all(TOKEN not in r.text for r in responses)
    assert TOKEN not in capsys.readouterr().out


def test_openapi_models_have_no_token_output(app: FastAPI) -> None:  # covers: AC-4
    schema = app.openapi()["components"]["schemas"]
    assert "token" not in schema["DiscordAccountOut"]["properties"]


async def test_sse_emits_account_status_with_data(
    factory: Factory, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-15
    # 没有 data 的 SSE 事件浏览器不会派发，所以 data 必须存在。
    broadcaster = Broadcaster()
    monkeypatch.setattr(routes, "HEARTBEAT_INTERVAL", 60.0)
    stream = routes.events(broadcaster, CurrentSession(b"x" * 32), factory, clock)  # pyright: ignore[reportArgumentType]
    iterator = aiter(stream)  # pyright: ignore[reportArgumentType, reportCallIssue, reportUnknownVariableType]
    assert (await anext(iterator)).event == "heartbeat"  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportAttributeAccessIssue]

    broadcaster.publish({"type": "account_status", "account_id": 1})
    event = await anext(iterator)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]

    assert event.event == "account_status"  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    assert event.data == {}  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
    await iterator.aclose()  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]


@pytest.mark.parametrize("length", [20, 512])
async def test_token_length_limits_are_inclusive(logged_in: httpx.AsyncClient, length: int) -> None:
    response = await logged_in.post(BASE, json={"label": "main", "token": "x" * length})
    assert response.status_code == 201


@pytest.mark.parametrize("length", [19, 513])
async def test_token_just_outside_limits_is_rejected(
    logged_in: httpx.AsyncClient, length: int
) -> None:
    response = await logged_in.post(BASE, json={"label": "main", "token": "x" * length})
    assert response.json()["error"]["code"] == "token_format_invalid"


@pytest.mark.parametrize(("length", "code"), [(100, 201), (101, 422)])
async def test_label_length_counts_after_stripping(
    logged_in: httpx.AsyncClient, length: int, code: int
) -> None:
    response = await logged_in.post(BASE, json={"label": f"  {'x' * length}  ", "token": TOKEN})
    assert response.status_code == code


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"token": TOKEN}, id="missing-label"),
        pytest.param({"label": "x" * 101, "token": TOKEN}, id="label-too-long"),
        pytest.param({"label": "main", "token": [TOKEN]}, id="token-not-a-string"),
        pytest.param({"label": "main", "token": TOKEN, "extra": {"token": TOKEN}}, id="ok-extra"),
    ],
)
async def test_validation_errors_never_echo_the_token(
    logged_in: httpx.AsyncClient, body: dict[str, object]
) -> None:  # covers: AC-4
    response = await logged_in.post(BASE, json=body)
    assert TOKEN not in response.text


async def test_replace_token_rejects_bad_format_and_keeps_the_old_one(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await _add(logged_in)
    async with factory() as session:
        before = await session.scalar(select(DiscordAccount.token_ciphertext))

    response = await logged_in.put(f"{BASE}/token", json={"token": "Bot abc"})

    async with factory() as session:
        after = await session.scalar(select(DiscordAccount.token_ciphertext))
    assert response.json()["error"]["code"] == "token_format_invalid"
    assert before == after


async def test_replace_token_stores_new_ciphertext_and_bumps_version(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-4, AC-10
    await _add(logged_in)
    new_token = "another-valid-token.abc123"

    body = (await logged_in.put(f"{BASE}/token", json={"token": new_token})).json()

    async with factory() as session:
        row = await accounts.get_account(session)
    assert row is not None
    plain = decrypt(crypto_settings().key_ring, PURPOSE_DISCORD_TOKEN, row.token_ciphertext)
    assert plain == new_token.encode()
    assert row.config_version == 2
    assert body["status"] == "connecting"
    assert new_token not in str(body)


async def test_replace_token_while_disabled_stays_disabled(
    logged_in: httpx.AsyncClient,
) -> None:  # covers: AC-12
    await _add(logged_in)
    await logged_in.post(f"{BASE}/disable")

    body = (await logged_in.put(f"{BASE}/token", json={"token": TOKEN})).json()

    assert body["status"] == "disabled"


async def test_disable_twice_returns_200(logged_in: httpx.AsyncClient) -> None:
    await _add(logged_in)
    await logged_in.post(f"{BASE}/disable")

    response = await logged_in.post(f"{BASE}/disable")

    assert (response.status_code, response.json()["status"]) == (200, "disabled")


async def test_label_on_missing_account_is_404(logged_in: httpx.AsyncClient) -> None:
    response = await logged_in.put(f"{BASE}/label", json={"label": "x"})
    assert response.json()["error"]["code"] == "account_not_found"


async def test_snowflake_is_returned_as_a_string(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await _add(logged_in)
    # 超过 JS 的安全整数，用数字返回会丢精度。
    snowflake = 2**60 + 1
    async with factory() as session, session.begin():
        await accounts.record_identity(session, 1, 1, snowflake, "alt")

    account = (await logged_in.get(BASE)).json()["account"]

    assert account["discord_user_id"] == str(snowflake)
    assert account["discord_username"] == "alt"


@pytest.mark.parametrize(
    ("status", "column", "age", "stale"),
    [
        pytest.param("online", "last_heartbeat_at", "121 seconds", True, id="online-old-beat"),
        pytest.param("online", "last_heartbeat_at", "60 seconds", False, id="online-fresh-beat"),
        pytest.param("online", "last_heartbeat_at", None, False, id="online-no-beat"),
        pytest.param("connecting", "status_changed_at", "121 seconds", True, id="connecting-old"),
        pytest.param("reconnecting", "status_changed_at", "1 hour", False, id="reconnecting"),
    ],
)
async def test_collector_stale_uses_the_database_clock(
    logged_in: httpx.AsyncClient,
    factory: Factory,
    status: str,
    column: str,
    age: str | None,
    stale: bool,
) -> None:  # covers: AC-15
    await _add(logged_in)
    value = "NULL" if age is None else f"now() - interval '{age}'"
    async with factory() as session, session.begin():
        await session.execute(
            text(f"UPDATE discord_account SET status = :s, {column} = {value}"), {"s": status}
        )

    assert (await logged_in.get(BASE)).json()["account"]["collector_stale"] is stale


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "", {"label": "x", "token": TOKEN}),
        ("PUT", "/label", {"label": "x"}),
        ("PUT", "/token", {"token": TOKEN}),
        ("POST", "/disable", None),
        ("POST", "/enable", None),
        ("POST", "/reconnect", None),
        ("POST", "/switch-identity", None),
    ],
)
async def test_every_write_route_checks_origin(
    logged_in: httpx.AsyncClient, method: str, path: str, body: dict[str, str] | None
) -> None:  # covers: AC-16
    response = await logged_in.request(
        method, f"{BASE}{path}", json=body, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403


def test_discord_logger_stays_at_warning_even_in_debug(restore_logging: None) -> None:  # AC-4
    # discord 库的低级别日志带请求细节，不能跟着 LOG_LEVEL 打开。
    configure_logging("DEBUG")

    assert logging.getLogger("discord").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("discord.http").getEffectiveLevel() == logging.WARNING
