"""附件下载任务（spec 0011）：领取、下载、算哈希、存储、写结果。

挂在 collector 主进程里，和进程心跳并列。下载循环、换链接循环、心跳循环各自的最外层都
包 `except Exception`：出错记日志、等一会儿再继续，不会让进程退出（退出重启要消耗登录额度）。
网络操作都不在数据库事务里；写结果的事务里同时发 `message_changed`。
"""

import asyncio
import contextlib
import hashlib
import mimetypes
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.collector.refresh import RateLimited, Refreshed, Unavailable
from harvester.collector.supervisor import GatewayHandle
from harvester.db import media as media_db
from harvester.db.media import ClaimedMedia, NewStoredFile
from harvester.db.messages import notify_message_changed
from harvester.db.snapshots import MediaStatus
from harvester.db.system import record_media_worker_heartbeat
from harvester.media import policy
from harvester.media.storage import MediaStore
from harvester.media.urls import file_prefix, is_discord_hosted, object_key, strip_query

log = structlog.get_logger("harvester.collector.media")

HTTP_OK = 200
MAX_CONCURRENT_DOWNLOADS = 3
# 没有通知时每隔这么久看一次队列。
POLL_INTERVAL = 60.0
# 任一循环遇到意外异常后等这么久再继续（AC-22）。
ERROR_BACKOFF = 60.0
HEARTBEAT_INTERVAL = 30.0
# 1 分钟内超过 10 个不同的行收到 403：整体暂停 30 分钟（AC-10）。
BREAKER_WINDOW = 60.0
BREAKER_LIMIT = 10
PAUSE_DURATION = timedelta(minutes=30)
# 换链接：每批最多 50 个，两批之间至少隔 30 秒，每小时最多 600 个（AC-11）。
REFRESH_BATCH = 50
REFRESH_GAP = 30.0
REFRESH_PER_HOUR = 600
REFRESH_WINDOW = 3600.0
# Discord 暂时不可用（5xx、网络、401、403 等）时循环的退避：1、5、30 分钟。
UNAVAILABLE_BACKOFF = (60.0, 300.0, 1800.0)
CHUNK_SIZE = 64 * 1024
DOWNLOAD_TOTAL_TIMEOUT = 600.0
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


def new_http_client() -> httpx.AsyncClient:
    """不跟随重定向、不读代理环境变量；`identity` 让计数的就是文件本身的字节（AC-3）。"""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        timeout=DOWNLOAD_TIMEOUT,
        headers={"Accept-Encoding": "identity"},
    )


@dataclass
class PauseState:
    """403 熔断的内存状态。显式传入的可变容器，不是模块级变量。"""

    recent: dict[int, float] = field(default_factory=dict[int, float])
    until: datetime | None = None


@dataclass
class RefreshState:
    """换链接循环的内存状态：最近一小时发出的链接数，和连续不可用的次数。"""

    sent: deque[tuple[float, int]] = field(default_factory=deque[tuple[float, int]])
    failures: int = 0


@dataclass(frozen=True)
class MediaDeps:
    factory: async_sessionmaker[AsyncSession]
    store: MediaStore
    http: httpx.AsyncClient
    tmp_dir: Path
    wake: asyncio.Event
    pause: PauseState
    clock: Callable[[], datetime]
    monotonic: Callable[[], float] = time.monotonic
    gateway: GatewayHandle = field(default_factory=GatewayHandle)
    # 有行变成需要换链接时设上，换链接循环据此提前醒来。
    refresh_wake: asyncio.Event = field(default_factory=asyncio.Event)
    refresh_state: RefreshState = field(default_factory=RefreshState)


# ---- 下载一个文件 --------------------------------------------------------------------


@dataclass(frozen=True)
class Downloaded:
    path: Path
    sha256: bytes
    size_bytes: int


@dataclass(frozen=True)
class HttpFailure:
    """`status` 为空表示没有响应；`code` 是写进 last_error 的错误代码。"""

    status: int | None
    code: str


@dataclass(frozen=True)
class AbortedTooLarge:
    limit: int


DownloadOutcome = Downloaded | HttpFailure | AbortedTooLarge


async def download_to_file(
    client: httpx.AsyncClient, url: str, target: Path, max_bytes: int
) -> DownloadOutcome:
    """流式下载到 `target`，边写边算 SHA-256，超过上限立刻中止。"""
    digest = hashlib.sha256()
    size = 0
    try:
        async with asyncio.timeout(DOWNLOAD_TOTAL_TIMEOUT), client.stream("GET", url) as response:
            if response.status_code != HTTP_OK:
                return HttpFailure(response.status_code, f"http_{response.status_code}")
            with target.open("wb") as out:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        return AbortedTooLarge(max_bytes)
                    digest.update(chunk)
                    out.write(chunk)
    except (httpx.TimeoutException, TimeoutError):
        return HttpFailure(None, "timeout")
    except httpx.HTTPError:
        return HttpFailure(None, "network")
    return Downloaded(target, digest.digest(), size)


def guess_content_type(declared: str | None, filename: str) -> str:
    """Discord 给的类型，其次按文件名猜，最后是 octet-stream。"""
    if declared:
        return declared.split(";", 1)[0].strip().lower()
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


# ---- 写结果 --------------------------------------------------------------------------


async def _finish(
    deps: MediaDeps,
    row: ClaimedMedia,
    write: Callable[[AsyncSession], Awaitable[bool]],
    *,
    notify: bool,
) -> None:
    """一个事务：写结果；领取仍有效时发 `message_changed`。"""
    async with deps.factory() as session, session.begin():
        applied = await write(session)
        if applied and notify:
            await notify_message_changed(session, row.channel_id, (row.message_id,))
        if not applied:
            log.info("media.result_discarded", media_id=row.id)


async def _terminal(
    deps: MediaDeps,
    row: ClaimedMedia,
    status: MediaStatus,
    error: str | None,
    aborted_at_bytes: int | None = None,
) -> None:
    async def write(session: AsyncSession) -> bool:
        return await media_db.record_terminal(
            session,
            row.id,
            row.claimed_at,
            status,
            error=error,
            aborted_at_bytes=aborted_at_bytes,
        )

    await _finish(deps, row, write, notify=True)
    log.info("media.finished", media_id=row.id, kind=row.kind, status=status, error=error)


def _note_forbidden(deps: MediaDeps, media_id: int) -> None:
    """记一次 403；1 分钟内超过 10 个不同的行就暂停下载（AC-10）。"""
    now = deps.monotonic()
    recent = deps.pause.recent
    recent[media_id] = now
    for stale_id in [i for i, at in recent.items() if now - at > BREAKER_WINDOW]:
        del recent[stale_id]
    if len(recent) > BREAKER_LIMIT and deps.pause.until is None:
        deps.pause.until = deps.clock() + PAUSE_DURATION
        recent.clear()
        log.warning("media.paused", until=deps.pause.until.isoformat())


async def _fail(deps: MediaDeps, row: ClaimedMedia, failure: HttpFailure) -> None:
    recently_refreshed = policy.is_recently_refreshed(row.refreshed_at, row.db_now)
    action = policy.classify_download_error(failure.status, recently_refreshed=recently_refreshed)
    if failure.status == 403:
        _note_forbidden(deps, row.id)
    match action:
        case "refresh":

            async def mark(session: AsyncSession) -> bool:
                return await media_db.mark_needs_refresh(session, row.id, row.claimed_at)

            await _finish(deps, row, mark, notify=False)
            deps.refresh_wake.set()
            log.info("media.needs_refresh", media_id=row.id, reason=failure.code)
        case "gone":
            await _terminal(deps, row, "gone", failure.code)
        case "retry":
            attempts = row.attempts + 1
            final = attempts >= policy.MAX_ATTEMPTS

            async def retry(session: AsyncSession) -> bool:
                return await media_db.record_retry(
                    session,
                    row.id,
                    row.claimed_at,
                    attempts=attempts,
                    backoff_seconds=policy.next_backoff(attempts).total_seconds(),
                    final=final,
                    error=failure.code,
                )

            await _finish(deps, row, retry, notify=final)
            log.info(
                "media.retry", media_id=row.id, attempts=attempts, final=final, error=failure.code
            )


async def _store_downloaded(deps: MediaDeps, row: ClaimedMedia, done: Downloaded) -> None:
    prefix = file_prefix(row.channel_id, row.message_created_at)
    async with deps.factory() as session:
        existing = await media_db.find_stored_file_id(session, prefix, done.sha256)
    if existing is not None:  # 同频道同月内容相同：不上传，不占空间（AC-4）。
        await _record_stored(deps, row, file=None, existing_file_id=existing)
        return

    content_type = guess_content_type(row.declared_content_type, row.filename)
    async with deps.factory() as session:
        limits = await media_db.get_limits(session)
        used = await media_db.used_bytes(session)
    if used + done.size_bytes > limits.quota_bytes:
        await _terminal(deps, row, "no_space", None)
        return

    key = object_key(prefix, done.sha256.hex(), row.filename)
    try:
        await deps.store.put_file(key, done.path, content_type)
    except Exception as error:  # 存储边界：任何上传失败都按可重试处理
        log.warning("media.put_failed", media_id=row.id, error_type=type(error).__name__)
        await _fail(deps, row, HttpFailure(None, "r2_put"))
        return
    new = NewStoredFile(
        prefix=prefix,
        sha256=done.sha256,
        size_bytes=done.size_bytes,
        content_type=content_type,
        object_key=key,
        width=row.declared_width,
        height=row.declared_height,
    )
    await _record_stored(deps, row, file=new, existing_file_id=None)


async def _record_stored(
    deps: MediaDeps, row: ClaimedMedia, *, file: NewStoredFile | None, existing_file_id: int | None
) -> None:
    async def write(session: AsyncSession) -> bool:
        return await media_db.record_stored(
            session, row.id, row.claimed_at, file=file, existing_file_id=existing_file_id
        )

    await _finish(deps, row, write, notify=True)
    log.info("media.finished", media_id=row.id, kind=row.kind, status="stored")


async def process_row(deps: MediaDeps, row: ClaimedMedia) -> None:
    """一行的完整处理：准入、检查链接、下载、存储、写结果。"""
    async with deps.factory() as session:
        limits = await media_db.get_limits(session)
        used = await media_db.used_bytes(session)
    verdict = policy.admit(row.declared_size_bytes, limits.max_file_bytes, used, limits.quota_bytes)
    if verdict != "ok":
        await _terminal(deps, row, verdict, None)
        return
    if policy.link_needs_refresh(row.source_url, row.db_now):

        async def mark(session: AsyncSession) -> bool:
            return await media_db.mark_needs_refresh(session, row.id, row.claimed_at)

        await _finish(deps, row, mark, notify=False)
        deps.refresh_wake.set()
        return
    if not is_discord_hosted(row.source_url):  # 正常不会发生，但每次发请求前都检查（AC-3）
        await _terminal(deps, row, "gone", "not_discord_host")
        return

    with tempfile.NamedTemporaryFile(dir=deps.tmp_dir, delete=True) as tmp:
        outcome = await download_to_file(
            deps.http, row.source_url, Path(tmp.name), limits.max_file_bytes
        )
        match outcome:
            case HttpFailure():
                await _fail(deps, row, outcome)
            case AbortedTooLarge(limit=limit):
                await _terminal(deps, row, "too_large", None, aborted_at_bytes=limit)
            case Downloaded():
                await _store_downloaded(deps, row, outcome)


async def _process_guarded(deps: MediaDeps, row: ClaimedMedia) -> None:
    try:
        await process_row(deps, row)
    except Exception as error:  # 一行出错不能拖垮整个任务
        log.error(
            "media.process_failed",
            media_id=row.id,
            host_path=strip_query(row.source_url),
            error_type=type(error).__name__,
        )
        try:
            await _fail(deps, row, HttpFailure(None, "unexpected"))
        except Exception as inner:
            log.error("media.fail_write_failed", media_id=row.id, error_type=type(inner).__name__)


# ---- 循环 ----------------------------------------------------------------------------


async def prepare_start(factory: async_sessionmaker[AsyncSession], tmp_dir: Path) -> None:
    """启动：清空临时目录，把上次崩溃时领取的行放回队列（AC-18）。"""

    def reset_tmp() -> None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir.mkdir(parents=True, exist_ok=True)

    await asyncio.to_thread(reset_tmp)
    async with factory() as session, session.begin():
        released = await media_db.release_claims_on_start(session)
    log.info("media.started", released_claims=released)


async def _wait_for_work(wake: asyncio.Event, running: set["asyncio.Task[None]"]) -> None:
    waiter = asyncio.create_task(wake.wait())
    try:
        await asyncio.wait(
            {waiter, *running}, timeout=POLL_INTERVAL, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        waiter.cancel()
    wake.clear()


async def _pause_remaining(deps: MediaDeps) -> bool:
    """暂停期间不领取；到期后清掉暂停状态。"""
    until = deps.pause.until
    if until is None:
        return False
    if deps.clock() >= until:
        deps.pause.until = None
        log.info("media.resumed")
        return False
    return True


async def run_download_loop(deps: MediaDeps) -> None:
    running: set[asyncio.Task[None]] = set()
    try:
        await _download_forever(deps, running)
    finally:
        for task in running:  # 停止时正在下载的一并取消，领取标记由收尾放回队列
            task.cancel()


async def _download_forever(deps: MediaDeps, running: set["asyncio.Task[None]"]) -> None:
    while True:
        try:
            running.difference_update({task for task in running if task.done()})
            paused = await _pause_remaining(deps)
            claimed: tuple[ClaimedMedia, ...] = ()
            free = MAX_CONCURRENT_DOWNLOADS - len(running)
            if not paused and free > 0:
                async with deps.factory() as session, session.begin():
                    claimed = await media_db.claim_batch(session, free)
            for row in claimed:
                running.add(asyncio.create_task(_process_guarded(deps, row)))
            if claimed and len(running) < MAX_CONCURRENT_DOWNLOADS:
                continue  # 还有空位，马上再取一批
            await _wait_for_work(deps.wake, running)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # AC-22：意外异常不退出进程
            log.error("media.loop_failed", loop="download", error_type=type(error).__name__)
            await asyncio.sleep(ERROR_BACKOFF)


async def run_heartbeat_loop(deps: MediaDeps) -> None:
    while True:
        try:
            async with deps.factory() as session, session.begin():
                await record_media_worker_heartbeat(session, deps.pause.until)
            await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("media.loop_failed", loop="heartbeat", error_type=type(error).__name__)
            await asyncio.sleep(ERROR_BACKOFF)


# ---- 换链接循环 ----------------------------------------------------------------------


def hourly_budget(
    sent: Sequence[tuple[float, int]], now: float, *, limit: int = REFRESH_PER_HOUR
) -> tuple[int, float]:
    """最近一小时还能换多少个；额度用完时返回要等多久最早的一批才滑出窗口。"""
    recent = [(at, count) for at, count in sent if now - at < REFRESH_WINDOW]
    used = sum(count for _, count in recent)
    if used < limit:
        return limit - used, 0.0
    oldest = min(at for at, _ in recent)
    return 0, max(oldest + REFRESH_WINDOW - now, 1.0)


@dataclass(frozen=True)
class RefreshStep:
    """一轮之后要等多久。`interruptible` 为真时有新行要换链接就提前醒来。"""

    wait: float
    interruptible: bool


async def refresh_once(deps: MediaDeps) -> RefreshStep:
    """取一批需要换链接的行，用小号会话换，按结果写回（AC-11）。"""
    idle = RefreshStep(POLL_INTERVAL, interruptible=True)
    session_handle = deps.gateway.session
    if session_handle is None:  # 账号不在线：这些行等账号上线再处理
        return idle
    state = deps.refresh_state
    now = deps.monotonic()
    remaining, wait = hourly_budget(state.sent, now)
    if remaining <= 0:
        return RefreshStep(wait, interruptible=False)
    async with deps.factory() as session:
        rows = await media_db.list_refresh_batch(session, min(REFRESH_BATCH, remaining))
    if not rows:
        return idle

    result = await session_handle.refresh_attachment_urls([row.source_url for row in rows])
    match result:
        case Refreshed(urls=urls):
            async with deps.factory() as session, session.begin():
                refreshed, gone = await media_db.apply_refresh(session, rows, urls)
            state.sent.append((now, len(rows)))
            state.failures = 0
            log.info("media.refresh_batch", requested=len(rows), refreshed=refreshed, gone=gone)
            return RefreshStep(REFRESH_GAP, interruptible=False)
        case RateLimited(retry_after=retry_after):
            log.warning("media.refresh_rate_limited", retry_after=retry_after)
            return RefreshStep(max(retry_after, 1.0), interruptible=False)
        case Unavailable(reason=reason):
            state.failures += 1
            backoff = UNAVAILABLE_BACKOFF[min(state.failures, len(UNAVAILABLE_BACKOFF)) - 1]
            log.warning("media.refresh_unavailable", reason=reason, backoff=backoff)
            return RefreshStep(backoff, interruptible=False)


async def run_refresh_loop(deps: MediaDeps) -> None:
    while True:
        try:
            step = await refresh_once(deps)
            if step.interruptible:
                deps.refresh_wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(deps.refresh_wake.wait(), timeout=step.wait)
            else:
                await asyncio.sleep(step.wait)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # AC-22
            log.error("media.loop_failed", loop="refresh", error_type=type(error).__name__)
            await asyncio.sleep(ERROR_BACKOFF)
