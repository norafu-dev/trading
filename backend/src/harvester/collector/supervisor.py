"""监督循环（spec 0005）：一次只跑一个会话，所有数据库写入都在这里按队列顺序执行。"""

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.collector.events import (
    ChannelsWake,
    Disconnected,
    FullSync,
    HeartbeatDue,
    IdentifyRequested,
    LoggedIn,
    Ready,
    RequiredAction,
    Resumed,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
    Wake,
)
from harvester.collector.failures import (
    BACKOFF_MAX,
    ConnectionFailure,
    backoff_max,
    classify_failure,
    next_delay,
)
from harvester.crypto import PURPOSE_DISCORD_TOKEN, DecryptError, KeyRing, decrypt
from harvester.db import account as accounts
from harvester.db import channel_sync
from harvester.db.account import AccountRow, LoginBudgetExhausted
from harvester.db.coverage_plan import align_coverage
from harvester.db.ingest import close_stale_coverage
from harvester.db.snapshots import AccountStatus

log = structlog.get_logger("harvester.collector.supervisor")

HEARTBEAT_INTERVAL = 30.0
# 启动时距离上次登录尝试不到这么久就先等，防止崩溃循环连续消耗额度。
CRASH_GUARD_SECONDS = 60.0
# 从 READY 或 RESUMED 算起稳定这么久，退避计数清零。
STABLE_RESET = timedelta(seconds=300)
# 上一次连接稳定这么久，服务端让重连时可以立即重连。
STABLE_IMMEDIATE = timedelta(seconds=60)
BUDGET_ERROR = "login_budget_exhausted"

_NO_LOGIN: frozenset[AccountStatus] = frozenset(
    {"disabled", "token_invalid", "banned", "verification_required", "account_mismatch"}
)


class GatewaySession(Protocol):
    async def run_session(self, token: str) -> None: ...

    async def close(self) -> None: ...


class UnexpectedFailureError(Exception):
    """分类表之外的失败。消息只有类型名，不带任何响应内容。"""


@dataclass(frozen=True)
class Deps:
    factory: async_sessionmaker[AsyncSession]
    key_ring: KeyRing
    new_session: Callable[
        [int, "asyncio.Queue[SupervisorEvent]", Callable[[], datetime]], GatewaySession
    ]
    clock: Callable[[], datetime]
    uniform: Callable[[float, float], float]
    # NOTIFY 和轮询都只是把它设上；监督循环自己读库比较版本。
    wake: asyncio.Event
    # 频道开关变了的 NOTIFY 和轮询把它设上；只触发覆盖对齐，不读账号，不打断退避等待。
    channels_wake: asyncio.Event
    heartbeat_interval: float = HEARTBEAT_INTERVAL
    crash_guard_seconds: float = CRASH_GUARD_SECONDS


@dataclass(frozen=True)
class LoopState:
    """跨会话保留的状态。"""

    version: int | None = None
    attempt: int = 0
    generation: int = 0
    pending_delay: float = 0.0
    # 未恢复的断线时间：第一次断线时记下，只在 RESUMED 或新会话 READY 时清除。
    disconnected_at: datetime | None = None


@dataclass(frozen=True)
class SessionState:
    loop: LoopState
    status: AccountStatus
    ready_seen: bool = False
    stable_since: datetime | None = None
    last_stable: timedelta = timedelta(0)
    done: bool = False


async def _write_status(
    deps: Deps,
    account_id: int,
    version: int,
    status: AccountStatus,
    last_error: str | None,
    retry_at: datetime | None = None,
) -> bool:
    async with deps.factory() as session, session.begin():
        result = await accounts.record_status(
            session, account_id, version, status, last_error, retry_at
        )
    return result == "updated"


async def _write_failure(
    deps: Deps, account: AccountRow, failure: ConnectionFailure
) -> tuple[bool, bool]:
    """按分类表写状态，返回 (写入成功, 要停止登录)。"""
    verdict = classify_failure(failure)
    if verdict is None:
        raise UnexpectedFailureError(failure.kind)
    written = await _write_status(
        deps, account.id, account.config_version, verdict.status, verdict.last_error
    )
    return written, verdict.stops_login


def _next_attempt(
    deps: Deps, loop: LoopState, retry_after: float | None, maximum: float = BACKOFF_MAX
) -> tuple[LoopState, float]:
    attempt = loop.attempt + 1
    delay = next_delay(attempt, deps.uniform, maximum)
    return replace(loop, attempt=attempt), max(delay, retry_after or 0.0)


async def _close_coverage(deps: Deps, account_id: int, at: datetime) -> None:
    async with deps.factory() as session, session.begin():
        await accounts.close_account_coverage(session, account_id, at)


async def _on_ready(
    deps: Deps, account: AccountRow, state: SessionState, event: Ready
) -> SessionState:
    if event.required_action:
        return await _on_required_action(deps, account, state)
    now = deps.clock()
    # 覆盖段不在这里打开：紧接着的 FullSync 对齐之后才打开（spec 0006）。
    if not await _write_status(deps, account.id, account.config_version, "online", None):
        return replace(state, done=True)
    log.info("collector.online", generation=event.generation)
    return replace(
        state,
        status="online",
        ready_seen=True,
        stable_since=now,
        loop=replace(state.loop, disconnected_at=None),
    )


async def _on_resumed(deps: Deps, account: AccountRow, state: SessionState) -> SessionState:
    if not state.ready_seen:
        return state
    if not await _write_status(deps, account.id, account.config_version, "online", None):
        return replace(state, done=True)
    log.info("collector.resumed")
    resumed = replace(
        state,
        status="online",
        stable_since=deps.clock(),
        loop=replace(state.loop, disconnected_at=None),
    )
    await _align(deps, account, resumed)
    return resumed


async def _align(deps: Deps, account: AccountRow, state: SessionState) -> None:
    """只在会话 online 时对齐；reconnecting 期间既不开也不关（AC-7）。"""
    if state.status != "online":
        return
    async with deps.factory() as session, session.begin():
        plan = await align_coverage(session, account.id, deps.clock())
    if plan.to_open or plan.to_close:
        log.info("collector.coverage_aligned", opened=len(plan.to_open), closed=len(plan.to_close))


async def _on_full_sync(
    deps: Deps, account: AccountRow, state: SessionState, event: FullSync
) -> SessionState:
    now = deps.clock()
    present = frozenset(s.guild.id for s in event.states) | event.unavailable_ids
    async with deps.factory() as session, session.begin():
        # 先写同步时间：版本不符说明账号已经换了身份，这批快照不能再写。
        if not await channel_sync.record_synced(session, account.id, account.config_version, now):
            return replace(state, done=True)
        for guild_state in event.states:
            await channel_sync.apply_guild_state(session, account.id, guild_state, now)
        await channel_sync.mark_missing_guilds_left(session, account.id, present, now)
        if state.status == "online":
            await align_coverage(session, account.id, now)
        await channel_sync.notify_channels_changed(session)
    log.info(
        "collector.full_sync", guilds=len(event.states), unavailable=len(event.unavailable_ids)
    )
    return state


async def _on_disconnected(
    deps: Deps, account: AccountRow, state: SessionState, event: Disconnected
) -> SessionState:
    loop = state.loop
    stable = event.at - state.stable_since if state.stable_since is not None else timedelta(0)
    if stable >= STABLE_RESET:
        loop = replace(loop, attempt=0)
    loop = replace(loop, disconnected_at=loop.disconnected_at or event.at)
    next_state = replace(state, loop=loop, stable_since=None, last_stable=stable)
    if state.status != "online":
        return next_state
    if not await _write_status(deps, account.id, account.config_version, "reconnecting", None):
        return replace(next_state, done=True)
    return replace(next_state, status="reconnecting")


async def _on_required_action(deps: Deps, account: AccountRow, state: SessionState) -> SessionState:
    await _write_failure(deps, account, ConnectionFailure("required_action"))
    return replace(state, done=True)


async def _on_logged_in(
    deps: Deps, account: AccountRow, state: SessionState, event: LoggedIn
) -> SessionState:
    async with deps.factory() as session, session.begin():
        result = await accounts.record_identity(
            session, account.id, account.config_version, event.user_id, event.username
        )
        if result == "mismatch":
            await accounts.record_status(
                session, account.id, account.config_version, "account_mismatch", "account_mismatch"
            )
    proceed = result == "updated"
    event.reply.set_result(proceed)
    return state if proceed else replace(state, done=True)


async def _on_retry(
    deps: Deps, account: AccountRow, state: SessionState, event: RetryNeeded
) -> SessionState:
    written, stops = await _write_failure(deps, account, event.failure)
    if not written or stops:
        event.reply.set_result(None)
        return replace(state, done=True)
    next_state = replace(state, status="reconnecting", last_stable=timedelta(0))
    if event.immediate_allowed and state.last_stable >= STABLE_IMMEDIATE:
        event.reply.set_result(0.0)
        return next_state
    maximum = backoff_max(
        event.failure, resume_ready=event.resume_ready, resume_rejected=event.resume_rejected
    )
    loop, delay = _next_attempt(deps, state.loop, event.failure.retry_after, maximum)
    event.reply.set_result(delay)
    return replace(next_state, loop=loop)


async def _on_identify(
    deps: Deps, account: AccountRow, state: SessionState, event: IdentifyRequested
) -> SessionState:
    """覆盖段要在预留额度之前关闭：不管预留成不成功，断线期间都不算已覆盖。"""
    disconnected_at = state.loop.disconnected_at
    if disconnected_at is not None:
        await _close_coverage(deps, account.id, disconnected_at)
    async with deps.factory() as session, session.begin():
        current = await accounts.get_account(session, for_update=True)
        if current is None or current.config_version != account.config_version:
            event.reply.set_result(False)
            return replace(state, done=True)
        reserved = await accounts.reserve_login_attempt(session, account.id)
        if isinstance(reserved, LoginBudgetExhausted):
            await accounts.record_status(
                session,
                account.id,
                account.config_version,
                "login_paused",
                BUDGET_ERROR,
                reserved.retry_at,
            )
    # 事务提交之后才放行 IDENTIFY，崩溃也不会少计。
    allowed = not isinstance(reserved, LoginBudgetExhausted)
    event.reply.set_result(allowed)
    return state if allowed else replace(state, done=True)


async def _on_ended(
    deps: Deps, account: AccountRow, state: SessionState, event: SessionEnded
) -> SessionState:
    if event.unexpected is not None:
        raise event.unexpected
    done = replace(state, done=True)
    if event.failure is None:
        return done
    written, stops = await _write_failure(deps, account, event.failure)
    if not written or stops:
        return done
    loop, delay = _next_attempt(deps, state.loop, event.failure.retry_after)
    return replace(done, loop=replace(loop, pending_delay=delay))


async def _on_wake(deps: Deps, account: AccountRow, state: SessionState) -> SessionState:
    async with deps.factory() as session:
        current = await accounts.get_account(session)
    if current is None or current.config_version != account.config_version:
        return replace(state, done=True)
    return state


async def _on_heartbeat(deps: Deps, account: AccountRow, state: SessionState) -> SessionState:
    if state.status != "online":
        return state
    try:
        async with deps.factory() as session, session.begin():
            await accounts.record_heartbeat(session, account.id, account.config_version)
        # 顺带一次覆盖对齐，通知丢了也能在 30 秒内补上。
        await _align(deps, account, state)
    except (SQLAlchemyError, OSError) as exc:
        # 单次失败只记日志；数据库真的断了时，由锁连接探测触发退出。
        log.warning("collector.heartbeat_failed", error_type=type(exc).__name__)
    return state


async def _on_channels_wake(deps: Deps, account: AccountRow, state: SessionState) -> SessionState:
    await _align(deps, account, state)
    return state


def _cancel_reply(event: SupervisorEvent) -> None:
    if isinstance(event, LoggedIn | RetryNeeded | IdentifyRequested):
        event.reply.cancel()


async def handle_event(
    deps: Deps, account: AccountRow, state: SessionState, event: SupervisorEvent
) -> SessionState:
    generation = getattr(event, "generation", state.loop.generation)
    if generation != state.loop.generation:
        _cancel_reply(event)
        return state
    match event:
        case Ready():
            return await _on_ready(deps, account, state, event)
        case Resumed():
            return await _on_resumed(deps, account, state)
        case Disconnected():
            return await _on_disconnected(deps, account, state, event)
        case RequiredAction():
            return await _on_required_action(deps, account, state)
        case LoggedIn():
            return await _on_logged_in(deps, account, state, event)
        case RetryNeeded():
            return await _on_retry(deps, account, state, event)
        case IdentifyRequested():
            return await _on_identify(deps, account, state, event)
        case SessionEnded():
            return await _on_ended(deps, account, state, event)
        case FullSync():
            return await _on_full_sync(deps, account, state, event)
        case Wake():
            return await _on_wake(deps, account, state)
        case ChannelsWake():
            return await _on_channels_wake(deps, account, state)
        case HeartbeatDue():
            return await _on_heartbeat(deps, account, state)


async def _forward_wake(deps: Deps, queue: "asyncio.Queue[SupervisorEvent]") -> None:
    while True:
        await deps.wake.wait()
        deps.wake.clear()
        queue.put_nowait(Wake())


async def _forward_channels_wake(deps: Deps, queue: "asyncio.Queue[SupervisorEvent]") -> None:
    while True:
        await deps.channels_wake.wait()
        deps.channels_wake.clear()
        queue.put_nowait(ChannelsWake())


async def _tick_heartbeat(deps: Deps, queue: "asyncio.Queue[SupervisorEvent]") -> None:
    while True:
        await asyncio.sleep(deps.heartbeat_interval)
        queue.put_nowait(HeartbeatDue())


async def run_session(
    deps: Deps,
    account: AccountRow,
    token: str,
    loop_state: LoopState,
    queue: "asyncio.Queue[SupervisorEvent]",
) -> LoopState:
    """跑一个会话直到结束。不管怎么结束都关 client、关开放的覆盖段。"""
    generation = loop_state.generation + 1
    state = SessionState(
        loop=replace(loop_state, generation=generation, pending_delay=0.0), status=account.status
    )
    client = deps.new_session(generation, queue, deps.clock)
    tasks = (
        asyncio.create_task(client.run_session(token), name="discord-session"),
        asyncio.create_task(_forward_wake(deps, queue), name="session-wake"),
        asyncio.create_task(_forward_channels_wake(deps, queue), name="session-channels-wake"),
        asyncio.create_task(_tick_heartbeat(deps, queue), name="session-heartbeat"),
    )
    try:
        while not state.done:
            state = await handle_event(deps, account, state, await queue.get())
        return state.loop
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        await client.close()
        await _close_coverage(deps, account.id, state.loop.disconnected_at or deps.clock())
        log.info("collector.session_closed", generation=generation)


async def _read_account(deps: Deps) -> AccountRow | None:
    async with deps.factory() as session:
        return await accounts.get_account(session)


async def _wait_for_change(deps: Deps, version: int, seconds: float) -> None:
    """最多等这么多秒；账号版本变了就提前返回，轮询带来的唤醒不算。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while (remaining := deadline - loop.time()) > 0:
        deps.wake.clear()
        current = await _read_account(deps)
        if current is None or current.config_version != version:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(deps.wake.wait(), timeout=remaining)


async def _startup(deps: Deps) -> None:
    """关掉上个进程留下的覆盖段，状态归一，防崩溃循环。"""
    async with deps.factory() as session, session.begin():
        account = await accounts.get_account(session)
        if account is None:
            return
        await close_stale_coverage(session, account.id)
        if account.status in ("online", "reconnecting"):
            await accounts.record_status(
                session, account.id, account.config_version, "connecting", None
            )
        elapsed = await accounts.seconds_since_last_attempt(session, account.id)
    if account.status in _NO_LOGIN or elapsed is None or elapsed >= deps.crash_guard_seconds:
        return
    log.info("collector.crash_guard", wait_seconds=deps.crash_guard_seconds - elapsed)
    await asyncio.sleep(deps.crash_guard_seconds - elapsed)


async def _prepare_login(deps: Deps, account: AccountRow) -> str | None:
    """解密 token 并只读检查额度。不能登录时写好状态并返回 None，一个请求都不发。"""
    plain = decrypt(deps.key_ring, PURPOSE_DISCORD_TOKEN, account.token_ciphertext)
    if isinstance(plain, DecryptError):
        await _write_failure(deps, account, ConnectionFailure("undecryptable"))
        return None
    async with deps.factory() as session:
        budget = await accounts.check_login_budget(session, account.id)
    if isinstance(budget, LoginBudgetExhausted):
        await _write_status(
            deps, account.id, account.config_version, "login_paused", BUDGET_ERROR, budget.retry_at
        )
        return None
    if account.status == "login_paused" and not await _write_status(
        deps, account.id, account.config_version, "connecting", None
    ):
        return None
    return plain.decode()


async def run_supervisor(deps: Deps) -> None:
    """一直运行，直到被取消；写库失败的异常会传出去，由进程退出和 Compose 重启兜底。"""
    await _startup(deps)
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    loop = LoopState()
    while True:
        # 先清再读：读完之后才到的通知不会丢。
        deps.wake.clear()
        account = await _read_account(deps)
        if account is None or account.status in _NO_LOGIN:
            loop = replace(loop, version=None, pending_delay=0.0)
            await deps.wake.wait()
            continue
        if account.config_version != loop.version:
            # 配置变了：退避从头开始；未恢复的断线时间留着，它决定覆盖段的关闭时间。
            loop = replace(loop, version=account.config_version, attempt=0, pending_delay=0.0)
        if loop.pending_delay > 0:
            delay = loop.pending_delay
            loop = replace(loop, pending_delay=0.0)
            await _wait_for_change(deps, account.config_version, delay)
            continue
        if account.status == "login_paused" and account.retry_at is not None:
            remaining = (account.retry_at - account.db_now).total_seconds()
            if remaining > 0:
                await _wait_for_change(deps, account.config_version, remaining)
                current = await _read_account(deps)
                if current is None or current.config_version != account.config_version:
                    continue
                account = current
        token = await _prepare_login(deps, account)
        if token is None:
            continue
        # 解密之后再读一次：版本变了说明手里的 token 可能已经旧了，回到开头重来。
        latest = await _read_account(deps)
        if latest is None or latest.config_version != account.config_version:
            continue
        if latest.status in _NO_LOGIN or latest.status == "login_paused":
            continue
        loop = await run_session(deps, latest, token, loop, queue)
