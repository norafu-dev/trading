"""历史任务循环（spec 0012）：一次只跑一个任务，从新到旧一页一页抓，写库和断点在同一个事务里。

挂在 collector 主进程里，和附件下载任务并列，不放进 `waiters`：最外层 `except Exception`
记日志后等一会儿再继续，不会让进程退出。网络请求都不在数据库事务里。
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.collector.history_page import (
    AccountRisk,
    Fetched,
    Forbidden,
    NotFound,
    RateLimited,
    Unavailable,
    Unexpected,
)
from harvester.collector.supervisor import GatewayHandle, GatewaySession, is_transient_db_error
from harvester.db import history as history_db
from harvester.db.history import ClaimedJob
from harvester.db.history_ranges import snowflake_floor
from harvester.db.snapshots import HistoryError, HistoryWaitReason

log = structlog.get_logger("harvester.collector.history")

# 没有任务、暂停或离线时，最多等这么久再看一次（NOTIFY 丢了也有兜底）。
IDLE_WAIT = 30.0
# 循环遇到意外异常后等这么久再继续。
ERROR_BACKOFF = 60.0
# 5xx、网络、数据库瞬时错误：30 秒起翻倍、封顶 5 分钟；连续 5 次后放回，5 分钟内不再领取（AC-12）。
RETRY_BASE = 30.0
RETRY_MAX = 300.0
RETRY_LIMIT = 5
RETRY_COOL_DOWN = timedelta(minutes=5)


@dataclass(frozen=True)
class HistoryDeps:
    factory: async_sessionmaker[AsyncSession]
    gateway: GatewayHandle
    # `history_changed` 的 NOTIFY 设上：新任务、暂停和设置变化能及时生效。
    wake: asyncio.Event
    clock: Callable[[], datetime]
    # 每页间隔的随机数来源，测试里固定（AC-5）。
    uniform: Callable[[float, float], float]
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    idle_wait: float = IDLE_WAIT


@dataclass(frozen=True)
class Running:
    """手上的任务和连续重试的次数（成功提交一页后清零）。"""

    job: ClaimedJob
    retries: int = 0


def retry_delay(attempt: int) -> float:
    """第 `attempt` 次重试前等多久：30、60、120、240、300 秒。"""
    return min(RETRY_BASE * 2 ** (attempt - 1), RETRY_MAX)


async def _wait_wake(deps: HistoryDeps, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(deps.wake.wait(), timeout=seconds)


async def _release(
    deps: HistoryDeps, job: ClaimedJob, reason: str, cool_down: timedelta | None = None
) -> None:
    async with deps.factory() as session, session.begin():
        released = await history_db.release_job(session, job.id, cool_down)
    if released:
        log.info("history.job_released", job_id=job.id, reason=reason)


async def _fail(deps: HistoryDeps, job: ClaimedJob, error: HistoryError) -> None:
    async with deps.factory() as session, session.begin():
        failed = await history_db.fail_job(session, job.id, error)
    if failed:
        log.info("history.job_failed", job_id=job.id, channel_id=job.channel_id, error=error)


async def _set_wait(
    deps: HistoryDeps, job: ClaimedJob, reason: HistoryWaitReason | None, until: datetime | None
) -> bool:
    async with deps.factory() as session, session.begin():
        return await history_db.set_job_wait(session, job.id, reason, until)


async def _wait_then_resume(
    deps: HistoryDeps, current: Running, reason: HistoryWaitReason, seconds: float
) -> Running | None:
    """在任务行上写等待原因，等完清空；行已经不是 `running` 就放下它。"""
    until = deps.clock() + timedelta(seconds=seconds)
    if not await _set_wait(deps, current.job, reason, until):
        return None
    log.info("history.waiting", job_id=current.job.id, wait_reason=reason, wait_until=until)
    await deps.sleep(seconds)
    if not await _set_wait(deps, current.job, None, None):
        return None
    return current


async def _retry_later(deps: HistoryDeps, current: Running, reason: str) -> Running | None:
    attempt = current.retries + 1
    if attempt >= RETRY_LIMIT:
        await _release(deps, current.job, f"retry_limit:{reason}", RETRY_COOL_DOWN)
        return None
    resumed = await _wait_then_resume(
        deps, replace(current, retries=attempt), "retrying", retry_delay(attempt)
    )
    return resumed


async def _on_fetched(deps: HistoryDeps, current: Running, page: Fetched) -> Running | None:
    job = current.job
    if job.cursor_message_id is None and page.raw_count == 0:
        # 能看到频道但没有「读取消息历史」权限时 Discord 返回空列表，不能当成没有消息（AC-3a）。
        async with deps.factory() as session:
            suspicious = await history_db.has_stored_messages(
                session, job.channel_id, job.range_start, job.range_end
            )
        if suspicious:
            await _fail(deps, job, "suspect_empty")
            return None
    try:
        async with deps.factory() as session, session.begin():
            committed = await history_db.commit_page(
                session,
                job,
                page.messages,
                page.returned_ids,
                page.raw_count,
                page.request_count,
                deps.clock(),
            )
    except Exception as exc:
        if not is_transient_db_error(exc):
            raise
        log.warning("history.commit_retry", job_id=job.id, error_type=type(exc).__name__)
        return await _retry_later(deps, current, "database")
    if committed.status == "done":
        log.info("history.job_done", job_id=job.id, channel_id=job.channel_id)
        return None
    if committed.status != "running":
        return None
    return Running(replace(job, cursor_message_id=committed.cursor_message_id, preempted_count=0))


async def fetch_step(
    deps: HistoryDeps, gateway: GatewaySession, account_id: int, current: Running
) -> Running | None:
    """抓一页并处理结果；返回 None 表示放下了这个任务（完成、失败、放回或被 api 改了状态）。"""
    job = current.job
    async with deps.factory() as session:
        readable = await history_db.channel_readable(session, account_id, job.channel_id)
    if not readable:
        await _fail(deps, job, "no_access")
        return None
    before_id = job.cursor_message_id or snowflake_floor(job.range_end)
    match await gateway.fetch_history_page(job.channel_id, before_id):
        case Fetched() as page:
            return await _on_fetched(deps, current, page)
        case RateLimited(retry_after=retry_after):
            log.info("history.rate_limited", job_id=job.id, retry_after=retry_after)
            return await _wait_then_resume(deps, current, "rate_limited", retry_after)
        case Forbidden():
            await _fail(deps, job, "no_access")
            return None
        case NotFound():
            await _fail(deps, job, "channel_deleted")
            return None
        case Unavailable(reason=reason):
            return await _retry_later(deps, current, reason)
        case AccountRisk(reason=reason):
            async with deps.factory() as session, session.begin():
                await history_db.set_history_paused(session, "account_risk")
                await history_db.release_job(session, job.id)
            log.warning("history.paused_account_risk", job_id=job.id, reason=reason)
            return None
        case Unexpected(error_type=error_type):
            log.error("history.job_unexpected", job_id=job.id, error_type=error_type)
            await _fail(deps, job, "unexpected")
            return None


async def _claim(deps: HistoryDeps, account_id: int) -> Running | None:
    async with deps.factory() as session, session.begin():
        job = await history_db.claim_next_job(session, account_id)
    if job is None:
        return None
    log.info("history.job_claimed", job_id=job.id, channel_id=job.channel_id, origin=job.origin)
    return Running(job)


async def _run(deps: HistoryDeps) -> None:
    # 只有这个循环会把任务改成 running：开头先把上次留下的放回去。
    async with deps.factory() as session, session.begin():
        await history_db.reset_running_on_start(session)
    current: Running | None = None
    while True:
        # 先清再读：读完之后才到的通知不会丢。
        deps.wake.clear()
        async with deps.factory() as session:
            settings = await history_db.get_history_settings(session)
        gateway, account_id = deps.gateway.session, deps.gateway.account_id
        if settings.paused_reason is not None or gateway is None or account_id is None:
            if current is not None:
                reason = "paused" if settings.paused_reason is not None else "offline"
                await _release(deps, current.job, reason)
                current = None
            await _wait_wake(deps, deps.idle_wait)
            continue
        if current is None:
            current = await _claim(deps, account_id)
            if current is None:
                await _wait_wake(deps, deps.idle_wait)
                continue
        current = await fetch_step(deps, gateway, account_id, current)
        # 任务失败之后也等一个正常的页间隔再领下一个（AC-12）。
        await deps.sleep(deps.uniform(settings.page_delay_min_s, settings.page_delay_max_s))


async def run_history_loop(deps: HistoryDeps) -> None:
    """一直运行，直到被取消；意外异常只记类型，等一会儿从头再来。"""
    while True:
        try:
            await _run(deps)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("history.loop_failed", error_type=type(exc).__name__)
            await deps.sleep(ERROR_BACKOFF)


async def release_on_stop(factory: async_sessionmaker[AsyncSession]) -> None:
    """停止时尽力把正在跑的任务放回去；做不到也没关系，下次启动会放回。"""
    with contextlib.suppress(Exception):
        async with asyncio.timeout(2.0), factory() as session, session.begin():
            if await history_db.reset_running_on_start(session):
                log.info("history.job_released", reason="shutdown")
