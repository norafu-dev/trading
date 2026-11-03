"""认证表的读写。调用方负责事务；时间都由参数传入，不用数据库的 now()。"""

from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.api.auth_core import (
    FAILURE_WINDOW,
    GLOBAL_MAX_FAILURES,
    GLOBAL_WINDOW,
    LOGIN_EVENT_RETENTION,
    RATE_LIMITED_LOG_INTERVAL,
    SESSION_ABSOLUTE_LIMIT,
    SESSION_IDLE_LIMIT,
    SETUP_CODE_LIFETIME,
    RequestContext,
    hash_token,
    new_session_token,
    new_setup_code,
    remaining_wait_seconds,
)
from harvester.db.models import (
    DashboardCredential,
    DashboardSession,
    DashboardSetupCode,
    LoginEvent,
)
from harvester.db.snapshots import LoginOutcome

# 和 raw.COLLECTOR_LOCK_KEY 不同；登录和设置请求在这把事务级锁下一个一个来。
AUTH_LOCK_KEY = 7_204_001
FAILURE_OUTCOMES: tuple[LoginOutcome, ...] = ("bad_credentials", "setup_bad_code")
SINGLE_ROW_ID = 1


async def lock_auth(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(AUTH_LOCK_KEY)))


async def record_event(
    session: AsyncSession,
    outcome: LoginOutcome,
    ip: str | None,
    user_agent: str | None,
    now: datetime,
) -> None:
    session.add(LoginEvent(occurred_at=now, ip=ip, user_agent=user_agent, outcome=outcome))
    await session.flush()


async def _global_failures(session: AsyncSession, now: datetime) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(LoginEvent)
        .where(
            LoginEvent.outcome.in_(FAILURE_OUTCOMES), LoginEvent.occurred_at > now - GLOBAL_WINDOW
        )
    )
    return count or 0


async def _ip_wait_seconds(session: AsyncSession, ip: str, now: datetime) -> int:
    last_success = await session.scalar(
        select(func.max(LoginEvent.occurred_at)).where(
            LoginEvent.ip == ip, LoginEvent.outcome == "success"
        )
    )
    window_start = now - FAILURE_WINDOW
    cutoff = max(window_start, last_success) if last_success is not None else window_start
    row = (
        await session.execute(
            select(func.count(), func.max(LoginEvent.occurred_at)).where(
                LoginEvent.ip == ip,
                LoginEvent.outcome.in_(FAILURE_OUTCOMES),
                LoginEvent.occurred_at > cutoff,
            )
        )
    ).one()
    return remaining_wait_seconds(row[0], row[1], now)


async def _record_rate_limited(session: AsyncSession, ctx: RequestContext, now: datetime) -> None:
    recent = await session.scalar(
        select(LoginEvent.id)
        .where(
            LoginEvent.ip == ctx.ip,
            LoginEvent.outcome == "rate_limited",
            LoginEvent.occurred_at > now - RATE_LIMITED_LOG_INTERVAL,
        )
        .limit(1)
    )
    if recent is None:
        await record_event(session, "rate_limited", ctx.ip, ctx.user_agent, now)


async def check_rate_limit(session: AsyncSession, ctx: RequestContext, now: datetime) -> int | None:
    """先查全局再查这个 IP。被限速就记一条并返回要等的秒数，否则返回 None。"""
    if await _global_failures(session, now) > GLOBAL_MAX_FAILURES:
        wait = int(GLOBAL_WINDOW.total_seconds())
    else:
        wait = await _ip_wait_seconds(session, ctx.ip, now)
    if wait == 0:
        return None
    await _record_rate_limited(session, ctx, now)
    return wait


async def get_credential(session: AsyncSession) -> DashboardCredential | None:
    return await session.get(DashboardCredential, SINGLE_ROW_ID)


async def claim_totp_step(session: AsyncSession, step: int) -> bool:
    """影响 0 行说明这一步已经被用过。"""
    claimed = await session.scalar(
        update(DashboardCredential)
        .where(
            DashboardCredential.id == SINGLE_ROW_ID,
            (DashboardCredential.totp_last_step.is_(None))
            | (DashboardCredential.totp_last_step < step),
        )
        .values(totp_last_step=step)
        .returning(DashboardCredential.id)
    )
    return claimed is not None


async def get_session_row(session: AsyncSession, token_hash: bytes) -> DashboardSession | None:
    return await session.get(DashboardSession, token_hash)


async def touch_session(session: AsyncSession, token_hash: bytes, now: datetime) -> None:
    await session.execute(
        update(DashboardSession)
        .where(DashboardSession.token_hash == token_hash)
        .values(last_seen_at=now)
    )


async def delete_session(session: AsyncSession, token_hash: bytes) -> bool:
    deleted = await session.scalar(
        delete(DashboardSession)
        .where(DashboardSession.token_hash == token_hash)
        .returning(DashboardSession.token_hash)
    )
    return deleted is not None


async def create_session(
    session: AsyncSession, ctx: RequestContext, old_token_hash: bytes | None, now: datetime
) -> str:
    """建新会话并返回 cookie 值。请求带着旧 cookie 时先删旧行（防会话固定）。"""
    if old_token_hash is not None:
        await delete_session(session, old_token_hash)
    token = new_session_token()
    session.add(
        DashboardSession(
            token_hash=hash_token(token),
            created_at=now,
            last_seen_at=now,
            ip=ctx.ip,
            user_agent=ctx.user_agent,
        )
    )
    await session.flush()
    return token


async def purge_old_rows(session: AsyncSession, now: datetime) -> None:
    await session.execute(
        delete(LoginEvent).where(LoginEvent.occurred_at < now - LOGIN_EVENT_RETENTION)
    )
    await session.execute(
        delete(DashboardSession).where(
            (DashboardSession.last_seen_at <= now - SESSION_IDLE_LIMIT)
            | (DashboardSession.created_at <= now - SESSION_ABSOLUTE_LIMIT)
        )
    )


async def store_pending_totp(
    session: AsyncSession, code_hash: bytes, secret_enc: bytes, now: datetime
) -> bool:
    """设置码有效时写入待确认的 TOTP 密钥；影响 0 行就是设置码无效。"""
    updated = await session.scalar(
        update(DashboardSetupCode)
        .where(
            DashboardSetupCode.id == SINGLE_ROW_ID,
            DashboardSetupCode.code_hash == code_hash,
            DashboardSetupCode.expires_at > now,
        )
        .values(pending_totp_secret_enc=secret_enc)
        .returning(DashboardSetupCode.id)
    )
    return updated is not None


async def get_valid_setup_code(
    session: AsyncSession, code_hash: bytes, now: datetime
) -> DashboardSetupCode | None:
    return await session.scalar(
        select(DashboardSetupCode).where(
            DashboardSetupCode.id == SINGLE_ROW_ID,
            DashboardSetupCode.code_hash == code_hash,
            DashboardSetupCode.expires_at > now,
        )
    )


async def issue_setup_code(session: AsyncSession, now: datetime) -> str | None:
    """生成设置码并覆盖旧的；已经设置过时返回 None。"""
    await lock_auth(session)
    if await get_credential(session) is not None:
        return None
    code = new_setup_code()
    values = {
        "code_hash": hash_token(code),
        "pending_totp_secret_enc": None,
        "expires_at": now + SETUP_CODE_LIFETIME,
        "created_at": now,
    }
    await session.execute(
        insert(DashboardSetupCode)
        .values(id=SINGLE_ROW_ID, **values)
        .on_conflict_do_update(index_elements=[DashboardSetupCode.id], set_=values)
    )
    return code


async def reset_auth(session: AsyncSession, now: datetime) -> None:
    await lock_auth(session)
    await session.execute(delete(DashboardCredential))
    await session.execute(delete(DashboardSession))
    await session.execute(delete(DashboardSetupCode))
    await record_event(session, "reset", None, None, now)
