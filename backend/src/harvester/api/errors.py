"""统一错误格式：{"error": {"code", "message", "retry_after_seconds"?, "fields"?}}。"""

import socket
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException

log = structlog.get_logger("harvester.api")

# 连接阶段的错误 SQLAlchemy 不会包装，asyncpg 和系统网络错误会原样抛出。
# 这里不写 OSError：文件之类的无关错误不能报成数据库不可用。
_DATABASE_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    InterfaceError,
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
    asyncpg.InterfaceError,
    ConnectionError,
    TimeoutError,
    socket.gaierror,
)


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class UnauthenticatedError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_401_UNAUTHORIZED, "unauthenticated", "请先登录")


class RateLimitedError(ApiError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "尝试次数太多，请稍后再试",
            retry_after_seconds,
        )


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retry_after_seconds: int | None = None,
    fields: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    headers: dict[str, str] = {}
    if retry_after_seconds is not None:
        body["retry_after_seconds"] = retry_after_seconds
        headers["Retry-After"] = str(retry_after_seconds)
    if fields is not None:
        body["fields"] = fields
    return JSONResponse({"error": body}, status_code=status_code, headers=headers)


async def _handle_api_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(
        exc.status_code, exc.code, exc.message, retry_after_seconds=exc.retry_after_seconds
    )


async def _handle_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # 只留 loc 和 type：FastAPI 默认带的 input 和 ctx 会把密码和验证码回显出去。
    fields = [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", "输入不正确", fields=fields
    )


async def _handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return error_response(exc.status_code, code, str(exc.detail))


async def _handle_database_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    # 是 503 不是 401，前端不会误跳登录页。异常内容可能带连接串，不进日志。
    log.error("api.database_unavailable", error_type=type(exc).__name__)
    return error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE, "database_unavailable", "数据库暂时不可用"
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 异常内容可能带密钥，只记类型。
    log.error("api.unexpected_error", error_type=type(exc).__name__, path=request.url.path)
    return error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "服务器内部错误")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    for db_error in _DATABASE_UNAVAILABLE_ERRORS:
        app.add_exception_handler(db_error, _handle_database_unavailable)
    app.add_exception_handler(Exception, _handle_unexpected_error)
