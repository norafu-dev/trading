import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select

from harvester.api import auth_core as core
from harvester.api import auth_flows as flows
from harvester.api import auth_store as store
from harvester.api.deps import (
    ApiSettingsDep,
    ClockDep,
    CookieTokenHashDep,
    KeyRingDep,
    RequestContextDep,
    protected_router,
    session_row_is_valid,
)
from harvester.api.errors import ApiError, RateLimitedError
from harvester.config import ApiSettings
from harvester.db import SessionDep
from harvester.db.models import LoginEvent
from harvester.db.snapshots import LoginOutcome

PASSWORD_MIN_CHARS = 12
PASSWORD_MAX_CHARS = 128

public_router = APIRouter(prefix="/api")


def _normalized(value: str) -> str:
    # 非法的代理字符这里就会报错（UnicodeEncodeError 是 ValueError），变成 422，不会到哈希才崩。
    normalized = unicodedata.normalize("NFKC", value)
    normalized.encode()
    return normalized


def _new_password(value: str) -> str:
    if not PASSWORD_MIN_CHARS <= len(value) <= PASSWORD_MAX_CHARS:
        raise ValueError("password length out of range")
    return value


def _encodable(value: str) -> str:
    value.encode()
    return value


LoginPassword = Annotated[str, AfterValidator(_normalized)]
NewPassword = Annotated[str, AfterValidator(_normalized), AfterValidator(_new_password)]
SetupCode = Annotated[str, Field(max_length=256), AfterValidator(_encodable)]


class AuthState(BaseModel):
    state: Literal["setup_required", "unauthenticated", "authenticated"]


class LoginBody(BaseModel):
    password: LoginPassword
    totp_code: Annotated[str, Field(max_length=32), AfterValidator(_encodable)]


class SetupBeginBody(BaseModel):
    setup_code: SetupCode


class SetupBeginResult(BaseModel):
    otpauth_uri: str
    secret: str


class SetupCompleteBody(BaseModel):
    setup_code: SetupCode
    password: NewPassword
    totp_code: Annotated[str, Field(pattern=r"^[0-9]{6}$")]


class LoginEventOut(BaseModel):
    occurred_at: datetime
    ip: str | None
    user_agent: str | None
    outcome: LoginOutcome


class Liveness(BaseModel):
    status: Literal["ok"]


def _set_session_cookie(response: Response, settings: ApiSettings, value: str) -> None:
    response.set_cookie(
        core.session_cookie_name(settings.session_cookie_secure),
        value,
        max_age=core.SESSION_COOKIE_MAX_AGE,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _setup_code_invalid() -> ApiError:
    return ApiError(
        status.HTTP_403_FORBIDDEN,
        "setup_code_invalid",
        "设置码不对或已过期，请在服务器上重新运行命令",
    )


def _already_set_up() -> ApiError:
    return ApiError(status.HTTP_409_CONFLICT, "already_set_up", "已经设置过了")


def _totp_invalid() -> ApiError:
    return ApiError(status.HTTP_400_BAD_REQUEST, "totp_invalid", "验证码不对")


def _undecryptable() -> ApiError:
    return ApiError(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "服务器配置有问题")


@public_router.get("/livez", tags=["system"])
async def livez() -> Liveness:
    """只回答 ok，不查库，给外部监控用。"""
    return Liveness(status="ok")


@public_router.get("/auth/state", tags=["auth"])
async def auth_state(
    session: SessionDep, token_hash: CookieTokenHashDep, clock: ClockDep
) -> AuthState:
    if await store.get_credential(session) is None:
        return AuthState(state="setup_required")
    if token_hash is not None and await session_row_is_valid(session, token_hash, clock()):
        return AuthState(state="authenticated")
    return AuthState(state="unauthenticated")


@public_router.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def login(
    body: LoginBody,
    response: Response,
    session: SessionDep,
    ctx: RequestContextDep,
    token_hash: CookieTokenHashDep,
    keys: KeyRingDep,
    clock: ClockDep,
    settings: ApiSettingsDep,
) -> None:
    result = await flows.login(
        session,
        password=body.password,
        totp_code=body.totp_code,
        ctx=ctx,
        old_token_hash=token_hash,
        keys=keys,
        clock=clock,
    )
    match result:
        case flows.LoggedIn(cookie_value):
            _set_session_cookie(response, settings, cookie_value)
        case flows.RateLimited(seconds):
            raise RateLimitedError(seconds)
        case flows.SetupRequired():
            raise ApiError(status.HTTP_409_CONFLICT, "setup_required", "还没有完成设置")
        case flows.BadCredentials():
            raise ApiError(status.HTTP_401_UNAUTHORIZED, "bad_credentials", "密码或验证码不对")
        case flows.SecretUndecryptable():
            raise _undecryptable()


@public_router.post("/auth/setup/begin", tags=["auth"])
async def setup_begin(
    body: SetupBeginBody,
    session: SessionDep,
    ctx: RequestContextDep,
    keys: KeyRingDep,
    clock: ClockDep,
) -> SetupBeginResult:
    result = await flows.setup_begin(
        session, setup_code=body.setup_code, ctx=ctx, keys=keys, clock=clock
    )
    match result:
        case flows.TotpProvisioning(otpauth_uri, secret):
            return SetupBeginResult(otpauth_uri=otpauth_uri, secret=secret)
        case flows.RateLimited(seconds):
            raise RateLimitedError(seconds)
        case flows.AlreadySetUp():
            raise _already_set_up()
        case flows.SetupCodeInvalid():
            raise _setup_code_invalid()


@public_router.post("/auth/setup/complete", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def setup_complete(
    body: SetupCompleteBody,
    response: Response,
    session: SessionDep,
    ctx: RequestContextDep,
    token_hash: CookieTokenHashDep,
    keys: KeyRingDep,
    clock: ClockDep,
    settings: ApiSettingsDep,
) -> None:
    result = await flows.setup_complete(
        session,
        setup_code=body.setup_code,
        password=body.password,
        totp_code=body.totp_code,
        ctx=ctx,
        old_token_hash=token_hash,
        keys=keys,
        clock=clock,
    )
    match result:
        case flows.LoggedIn(cookie_value):
            _set_session_cookie(response, settings, cookie_value)
        case flows.RateLimited(seconds):
            raise RateLimitedError(seconds)
        case flows.AlreadySetUp():
            raise _already_set_up()
        case flows.SetupCodeInvalid():
            raise _setup_code_invalid()
        case flows.TotpInvalid():
            raise _totp_invalid()
        case flows.SecretUndecryptable():
            raise _undecryptable()


@public_router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def logout(
    response: Response,
    session: SessionDep,
    ctx: RequestContextDep,
    token_hash: CookieTokenHashDep,
    clock: ClockDep,
    settings: ApiSettingsDep,
) -> None:
    await flows.logout(session, token_hash=token_hash, ctx=ctx, clock=clock)
    response.delete_cookie(
        core.session_cookie_name(settings.session_cookie_secure),
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@protected_router.get("/auth/login-events", tags=["auth"])
async def login_events(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[LoginEventOut]:
    rows = await session.scalars(
        select(LoginEvent)
        .order_by(LoginEvent.occurred_at.desc(), LoginEvent.id.desc())
        .limit(limit)
    )
    return [
        LoginEventOut(
            occurred_at=row.occurred_at,
            # asyncpg 把 inet 读成 ipaddress 对象。
            ip=str(row.ip) if row.ip is not None else None,
            user_agent=row.user_agent,
            outcome=row.outcome,
        )
        for row in rows
    ]
