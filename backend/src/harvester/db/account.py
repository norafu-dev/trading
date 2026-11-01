"""账号表的读写（spec 0005）。这里的函数都不提交事务，可预期的结果用返回值表达。

api 的写操作和 collector 的状态写入都在同一事务里发 `discord_account_changed` 通知。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import close_realtime_coverage, open_realtime_coverage
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    DiscordAccount,
    DiscordLoginAttempt,
)
from harvester.db.raw import ACCOUNT_CHANGED_CHANNEL
from harvester.db.snapshots import AccountStatus

LOGIN_BUDGET = 10
LOGIN_BUDGET_WINDOW = timedelta(hours=24)
LOGIN_ATTEMPT_RETENTION = timedelta(days=7)
COLLECTOR_STALE_AFTER = timedelta(seconds=120)

NotFound = Literal["not_found"]
InvalidState = Literal["invalid_state"]

_RECONNECTABLE: frozenset[AccountStatus] = frozenset(
    {"token_invalid", "verification_required", "banned"}
)


@dataclass(frozen=True)
class AccountRow:
    id: int
    label: str
    token_ciphertext: bytes
    discord_user_id: int | None
    discord_username: str | None
    status: AccountStatus
    status_changed_at: datetime
    last_error: str | None
    last_heartbeat_at: datetime | None
    retry_at: datetime | None
    config_version: int
    # 读取时数据库的 now()：所有时间判断都以数据库时钟为准。
    db_now: datetime


@dataclass(frozen=True)
class LoginBudgetExhausted:
    retry_at: datetime


def is_collector_stale(row: AccountRow) -> bool:
    if row.status == "online":
        heartbeat = row.last_heartbeat_at
        return heartbeat is not None and row.db_now - heartbeat > COLLECTOR_STALE_AFTER
    if row.status == "connecting":
        return row.db_now - row.status_changed_at > COLLECTOR_STALE_AFTER
    return False


async def _notify(session: AsyncSession, account_id: int) -> None:
    await session.execute(
        text(
            "SELECT pg_notify(:channel, json_build_object('account_id', CAST(:id AS bigint))::text)"
        ),
        {"channel": ACCOUNT_CHANGED_CHANNEL, "id": account_id},
    )


async def get_account(session: AsyncSession, *, for_update: bool = False) -> AccountRow | None:
    stmt = select(DiscordAccount, func.now())
    # now() 是事务开始的时间；SSE 和轮询的读都是短事务，不影响判断。
    if for_update:
        stmt = stmt.with_for_update(of=DiscordAccount)
    result = (await session.execute(stmt.execution_options(populate_existing=True))).first()
    if result is None:
        return None
    account, db_now = result
    return AccountRow(
        id=account.id,
        label=account.label,
        token_ciphertext=account.token_ciphertext,
        discord_user_id=account.discord_user_id,
        discord_username=account.discord_username,
        status=account.status,
        status_changed_at=account.status_changed_at,
        last_error=account.last_error,
        last_heartbeat_at=account.last_heartbeat_at,
        retry_at=account.retry_at,
        config_version=account.config_version,
        db_now=db_now,
    )


async def _reread(session: AsyncSession) -> AccountRow:
    session.expire_all()
    row = await get_account(session)
    assert row is not None
    return row


async def create_account(
    session: AsyncSession, label: str, token_ciphertext: bytes, token_key_version: int
) -> AccountRow | Literal["exists"]:
    created = await session.scalar(
        pg_insert(DiscordAccount)
        .values(label=label, token_ciphertext=token_ciphertext, token_key_version=token_key_version)
        .on_conflict_do_nothing(index_elements=[text("(true)")])
        .returning(DiscordAccount.id)
    )
    if created is None:
        return "exists"
    await _notify(session, created)
    return await _reread(session)


async def _apply_api_write(
    session: AsyncSession, row: AccountRow, status: AccountStatus | None, **values: Any
) -> AccountRow:
    """api 改配置：加版本号、清错误、发通知。status 为 None 表示状态不变。"""
    status_values: dict[str, Any] = (
        {} if status is None else {"status": status, "status_changed_at": func.now()}
    )
    # 不改状态时 login_paused 不可能出现在这里：能走到这条路的只有 disabled。
    await session.execute(
        update(DiscordAccount)
        .where(DiscordAccount.id == row.id)
        .values(
            config_version=DiscordAccount.config_version + 1,
            last_error=None,
            retry_at=None,
            updated_at=func.now(),
            **status_values,
            **values,
        )
    )
    await _notify(session, row.id)
    return await _reread(session)


async def update_label(session: AsyncSession, label: str) -> AccountRow | NotFound:
    row = await get_account(session, for_update=True)
    if row is None:
        return "not_found"
    await session.execute(
        update(DiscordAccount)
        .where(DiscordAccount.id == row.id)
        .values(label=label, updated_at=func.now())
    )
    await _notify(session, row.id)
    return await _reread(session)


async def replace_token(
    session: AsyncSession, token_ciphertext: bytes, token_key_version: int
) -> AccountRow | NotFound:
    row = await get_account(session, for_update=True)
    if row is None:
        return "not_found"
    # 停用时只保存，不连接（AC-12）。
    status: AccountStatus | None = None if row.status == "disabled" else "connecting"
    return await _apply_api_write(
        session, row, status, token_ciphertext=token_ciphertext, token_key_version=token_key_version
    )


async def set_enabled(session: AsyncSession, enabled: bool) -> AccountRow | NotFound | InvalidState:
    row = await get_account(session, for_update=True)
    if row is None:
        return "not_found"
    if not enabled:
        if row.status == "disabled":
            return row
        return await _apply_api_write(session, row, "disabled")
    if row.status != "disabled":
        return "invalid_state"
    return await _apply_api_write(session, row, "connecting")


async def request_reconnect(session: AsyncSession) -> AccountRow | NotFound | InvalidState:
    row = await get_account(session, for_update=True)
    if row is None:
        return "not_found"
    if row.status not in _RECONNECTABLE:
        return "invalid_state"
    return await _apply_api_write(session, row, "connecting")


async def switch_identity(session: AsyncSession) -> AccountRow | NotFound | InvalidState:
    """清空身份和权限记录；消息、频道开关和 collector_account_id 不动（AC-11）。"""
    row = await get_account(session, for_update=True)
    if row is None:
        return "not_found"
    if row.status != "account_mismatch":
        return "invalid_state"
    await session.execute(
        delete(AccountChannelAccess).where(AccountChannelAccess.account_id == row.id)
    )
    return await _apply_api_write(
        session, row, "connecting", discord_user_id=None, discord_username=None
    )


async def _budget_state(session: AsyncSession, account_id: int) -> LoginBudgetExhausted | None:
    window = DiscordLoginAttempt.attempted_at > func.now() - LOGIN_BUDGET_WINDOW
    count, earliest = (
        await session.execute(
            select(func.count(), func.min(DiscordLoginAttempt.attempted_at)).where(
                DiscordLoginAttempt.account_id == account_id, window
            )
        )
    ).one()
    if count < LOGIN_BUDGET:
        return None
    return LoginBudgetExhausted(retry_at=earliest + LOGIN_BUDGET_WINDOW)


async def check_login_budget(
    session: AsyncSession, account_id: int
) -> Literal["available"] | LoginBudgetExhausted:
    return await _budget_state(session, account_id) or "available"


async def reserve_login_attempt(
    session: AsyncSession, account_id: int
) -> Literal["reserved"] | LoginBudgetExhausted:
    """锁账号行再计数，并发预留也不会超额。调用方要在发 IDENTIFY 之前提交。"""
    await session.execute(
        select(DiscordAccount.id).where(DiscordAccount.id == account_id).with_for_update()
    )
    await session.execute(
        delete(DiscordLoginAttempt).where(
            DiscordLoginAttempt.account_id == account_id,
            DiscordLoginAttempt.attempted_at < func.now() - LOGIN_ATTEMPT_RETENTION,
        )
    )
    exhausted = await _budget_state(session, account_id)
    if exhausted is not None:
        return exhausted
    await session.execute(insert(DiscordLoginAttempt).values(account_id=account_id))
    return "reserved"


async def seconds_since_last_attempt(session: AsyncSession, account_id: int) -> float | None:
    latest = func.max(DiscordLoginAttempt.attempted_at)
    elapsed = await session.scalar(
        select(func.extract("epoch", func.now() - latest)).where(
            DiscordLoginAttempt.account_id == account_id
        )
    )
    return float(elapsed) if elapsed is not None else None


async def record_status(
    session: AsyncSession,
    account_id: int,
    expected_version: int,
    status: AccountStatus,
    last_error: str | None,
    retry_at: datetime | None = None,
) -> Literal["updated", "stale_version"]:
    """collector 写状态。版本不符就放弃，不覆盖 api 的操作（AC-13）。"""
    current = (
        await session.execute(
            select(DiscordAccount.status, DiscordAccount.last_error, DiscordAccount.retry_at)
            .where(
                DiscordAccount.id == account_id, DiscordAccount.config_version == expected_version
            )
            .with_for_update()
        )
    ).first()
    if current is None:
        return "stale_version"
    status_changed = current.status != status
    if not status_changed and (current.last_error, current.retry_at) == (last_error, retry_at):
        if status == "online":
            await record_heartbeat(session, account_id, expected_version)
        return "updated"
    values: dict[str, Any] = {
        "status": status,
        "last_error": last_error,
        "retry_at": retry_at,
        "updated_at": func.now(),
    }
    if status_changed:
        values["status_changed_at"] = func.now()
    if status == "online":
        values["last_heartbeat_at"] = func.now()
    await session.execute(
        update(DiscordAccount).where(DiscordAccount.id == account_id).values(**values)
    )
    await _notify(session, account_id)
    return "updated"


async def record_identity(
    session: AsyncSession, account_id: int, expected_version: int, user_id: int, username: str
) -> Literal["updated", "stale_version", "mismatch"]:
    """login 成功后调用：没有身份就写入，相符就更新用户名，不符不写。"""
    known = (
        await session.execute(
            select(DiscordAccount.discord_user_id)
            .where(
                DiscordAccount.id == account_id, DiscordAccount.config_version == expected_version
            )
            .with_for_update()
        )
    ).first()
    if known is None:
        return "stale_version"
    if known.discord_user_id is not None and known.discord_user_id != user_id:
        return "mismatch"
    await session.execute(
        update(DiscordAccount)
        .where(DiscordAccount.id == account_id)
        .values(discord_user_id=user_id, discord_username=username, updated_at=func.now())
    )
    await _notify(session, account_id)
    return "updated"


async def record_heartbeat(session: AsyncSession, account_id: int, expected_version: int) -> None:
    await session.execute(
        update(DiscordAccount)
        .where(
            DiscordAccount.id == account_id,
            DiscordAccount.config_version == expected_version,
            DiscordAccount.status == "online",
        )
        .values(last_heartbeat_at=func.now())
    )


async def open_account_coverage(session: AsyncSession, account_id: int, at: datetime) -> int:
    """全新登录时，给这个账号负责且开着采集的频道各打开一段（spec 0002）。"""
    channel_ids = await session.scalars(
        select(Channel.id).where(
            Channel.collector_account_id == account_id, Channel.collect_enabled
        )
    )
    results = [await open_realtime_coverage(session, cid, at) for cid in channel_ids.all()]
    return results.count("opened")


async def close_account_coverage(session: AsyncSession, account_id: int, at: datetime) -> int:
    channel_ids = await session.scalars(
        select(ChannelCoverage.channel_id)
        .join(Channel, Channel.id == ChannelCoverage.channel_id)
        .where(
            Channel.collector_account_id == account_id,
            ChannelCoverage.source == "realtime",
            func.upper_inf(ChannelCoverage.span),
        )
    )
    results = [await close_realtime_coverage(session, cid, at) for cid in channel_ids.all()]
    return len(results) - results.count("not_open")
