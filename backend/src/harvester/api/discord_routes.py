"""Discord 账号接口（spec 0005）：只校验、加密和通知，不发任何 Discord 请求。"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field, SecretStr, field_validator

from harvester.api.deps import KeyRingDep
from harvester.api.errors import ApiError
from harvester.crypto import PURPOSE_DISCORD_TOKEN, encrypt
from harvester.db import SessionFactoryDep
from harvester.db import account as accounts
from harvester.db.account import AccountRow
from harvester.db.snapshots import AccountStatus

TOKEN_MIN_LENGTH = 20
TOKEN_MAX_LENGTH = 512

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承。
discord_router = APIRouter(prefix="/discord/account", tags=["discord"])


class AccountNotFoundError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_404_NOT_FOUND, "account_not_found", "还没有添加账号")


class InvalidAccountStateError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_409_CONFLICT, "invalid_account_state", "账号当前的状态不能执行这个操作"
        )


class AccountExistsError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_409_CONFLICT, "account_exists", "已经有一个账号了")


class TokenFormatError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "token_format_invalid", "token 的格式不对"
        )


def _token_is_well_formed(token: str) -> bool:
    # 只允许可见 ASCII，不能含空格，所以 `Bot xxx` 会被拒绝。
    return TOKEN_MIN_LENGTH <= len(token) <= TOKEN_MAX_LENGTH and all(
        "!" <= char <= "~" for char in token
    )


def _checked_token(raw: SecretStr) -> bytes:
    """格式不对单独报错，不走通用校验：通用校验的错误信息里不该出现 token 字段的细节。"""
    token = raw.get_secret_value().strip()
    if not _token_is_well_formed(token):
        raise TokenFormatError
    return token.encode()


Label = Annotated[str, Field(min_length=1, max_length=100)]


class _LabelBody(BaseModel):
    label: Label

    @field_validator("label", mode="before")
    @classmethod
    def _strip_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class AccountCreate(_LabelBody):
    token: SecretStr


class LabelUpdate(_LabelBody):
    pass


class TokenUpdate(BaseModel):
    token: SecretStr


class DiscordAccountOut(BaseModel):
    id: int
    label: str
    # 字符串：snowflake 超过 JS 的安全整数。
    discord_user_id: str | None
    discord_username: str | None
    status: AccountStatus
    status_changed_at: datetime
    last_error: str | None
    last_heartbeat_at: datetime | None
    retry_at: datetime | None
    collector_stale: bool


class AccountEnvelope(BaseModel):
    account: DiscordAccountOut | None


def _to_out(row: AccountRow) -> DiscordAccountOut:
    return DiscordAccountOut(
        id=row.id,
        label=row.label,
        discord_user_id=str(row.discord_user_id) if row.discord_user_id is not None else None,
        discord_username=row.discord_username,
        status=row.status,
        status_changed_at=row.status_changed_at,
        last_error=row.last_error,
        last_heartbeat_at=row.last_heartbeat_at,
        retry_at=row.retry_at,
        collector_stale=accounts.is_collector_stale(row),
    )


def _unwrap(result: AccountRow | Literal["not_found", "invalid_state"]) -> DiscordAccountOut:
    if result == "not_found":
        raise AccountNotFoundError
    if result == "invalid_state":
        raise InvalidAccountStateError
    return _to_out(result)


@discord_router.get("")
async def get_discord_account(factory: SessionFactoryDep) -> AccountEnvelope:
    async with factory() as session:
        row = await accounts.get_account(session)
    return AccountEnvelope(account=_to_out(row) if row is not None else None)


@discord_router.post("", status_code=status.HTTP_201_CREATED)
async def add_discord_account(
    body: AccountCreate, factory: SessionFactoryDep, keys: KeyRingDep
) -> DiscordAccountOut:
    ciphertext = encrypt(keys, PURPOSE_DISCORD_TOKEN, _checked_token(body.token))
    async with factory() as session, session.begin():
        result = await accounts.create_account(
            session, body.label, ciphertext, keys.current_version
        )
    if result == "exists":
        raise AccountExistsError
    return _to_out(result)


@discord_router.put("/label")
async def update_discord_label(body: LabelUpdate, factory: SessionFactoryDep) -> DiscordAccountOut:
    async with factory() as session, session.begin():
        return _unwrap(await accounts.update_label(session, body.label))


@discord_router.put("/token")
async def replace_discord_token(
    body: TokenUpdate, factory: SessionFactoryDep, keys: KeyRingDep
) -> DiscordAccountOut:
    ciphertext = encrypt(keys, PURPOSE_DISCORD_TOKEN, _checked_token(body.token))
    async with factory() as session, session.begin():
        return _unwrap(await accounts.replace_token(session, ciphertext, keys.current_version))


@discord_router.post("/disable")
async def disable_discord_account(factory: SessionFactoryDep) -> DiscordAccountOut:
    async with factory() as session, session.begin():
        return _unwrap(await accounts.set_enabled(session, False))


@discord_router.post("/enable")
async def enable_discord_account(factory: SessionFactoryDep) -> DiscordAccountOut:
    async with factory() as session, session.begin():
        return _unwrap(await accounts.set_enabled(session, True))


@discord_router.post("/reconnect")
async def reconnect_discord_account(factory: SessionFactoryDep) -> DiscordAccountOut:
    async with factory() as session, session.begin():
        return _unwrap(await accounts.request_reconnect(session))


@discord_router.post("/switch-identity")
async def switch_discord_identity(factory: SessionFactoryDep) -> DiscordAccountOut:
    async with factory() as session, session.begin():
        return _unwrap(await accounts.switch_identity(session))
