"""统一错误格式：意外错误和数据库不可用。"""

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from harvester.api.main import create_app
from tests.api.conftest import ORIGIN, UNUSED_LINK_BASE, api_settings, crypto_settings

SECRET_TEXT = "secret-in-exception"


async def request_raising(exc: Exception) -> httpx.Response:
    async def boom() -> None:
        raise exc

    app: FastAPI = create_app(api_settings(), crypto_settings(), media_link_base=UNUSED_LINK_BASE)
    app.add_api_route("/boom", boom)
    # Starlette 发完兜底响应后还会重新抛出异常，这里只看响应。
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": ORIGIN}
    ) as client:
        return await client.get("/boom")


@pytest.mark.parametrize(
    "exc", [ValueError(SECRET_TEXT), FileNotFoundError(SECRET_TEXT), PermissionError(SECRET_TEXT)]
)
async def test_unexpected_error_is_500_json_without_details(exc: Exception) -> None:
    with capture_logs() as logs:
        response = await request_raising(exc)
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "服务器内部错误"}}
    assert logs[-1]["event"] == "api.unexpected_error"
    assert logs[-1]["error_type"] == type(exc).__name__
    assert SECRET_TEXT not in str(logs)


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(SECRET_TEXT),
        TimeoutError(SECRET_TEXT),
        asyncpg.PostgresConnectionError(SECRET_TEXT),
        asyncpg.CannotConnectNowError(SECRET_TEXT),
    ],
)
async def test_connection_error_is_database_unavailable(exc: Exception) -> None:
    response = await request_raising(exc)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
