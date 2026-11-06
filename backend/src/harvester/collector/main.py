import asyncio
import contextlib
import random
import signal
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import asyncpg
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.collector import history_worker, media_worker
from harvester.collector.discord_client import new_client
from harvester.collector.ratelimit_watch import RateLimitLog, install_ratelimit_watch
from harvester.collector.supervisor import Deps, GatewayHandle, run_supervisor
from harvester.collector.wake_guard import WakeGuard, watch_sleep
from harvester.config import (
    LocalMediaSettings,
    get_crypto_settings,
    load_media_settings,
)
from harvester.db import get_engine, get_sessionmaker, raw
from harvester.db import media as media_db
from harvester.db.system import record_collector_heartbeat
from harvester.media.storage import MediaStore, R2Store, build_local_store, build_r2_client

log = structlog.get_logger("harvester.collector")

# How often the dedicated lock connection is probed while idle.
LOCK_CHECK_INTERVAL = 10.0
# NOTIFY 丢了也有兜底：这么久读一次账号。
ACCOUNT_POLL_INTERVAL = 30.0
# 进程心跳间隔（spec 0009 AC-3），和账号状态无关。
PROCESS_HEARTBEAT_INTERVAL = 30.0
# 收到 SIGTERM 后关闭会话的时限，短于 Compose 默认的 10 秒强杀（AC-17）。
SHUTDOWN_TIMEOUT = 8.0
# 锁丢了要尽快退出，不等数据库写入。
LOCK_LOST_TIMEOUT = 1.0

EXIT_OK = 0
EXIT_CRASH = 1
EXIT_LOCK_HELD = 3
EXIT_LOCK_LOST = 4


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _acquire_lock() -> asyncpg.Connection | None:
    """Take the session level advisory lock on a dedicated connection, or None if held."""
    conn = await raw.connect()
    acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", raw.COLLECTOR_LOCK_KEY)
    if not acquired:
        await conn.close()
        return None
    return conn


async def _probe_lock(lock_conn: asyncpg.Connection, lost: asyncio.Event) -> None:
    while not lost.is_set():
        try:
            await lock_conn.fetchval("SELECT 1")
        except (asyncpg.PostgresError, OSError, asyncpg.InterfaceError):
            lost.set()
            return
        await asyncio.sleep(LOCK_CHECK_INTERVAL)


async def _poll_account(wake: asyncio.Event) -> None:
    while True:
        await asyncio.sleep(ACCOUNT_POLL_INTERVAL)
        wake.set()


async def _beat_once(factory: async_sessionmaker[AsyncSession]) -> None:
    try:
        async with factory() as session, session.begin():
            await record_collector_heartbeat(session)
    except (SQLAlchemyError, OSError) as error:
        # 只记类型，不退出：数据库真的断了时由锁探测负责退出。
        log.warning("collector.process_heartbeat_failed", error_type=type(error).__name__)


async def _beat_process(factory: async_sessionmaker[AsyncSession]) -> None:
    """先写一次再循环；事件循环被阻塞时心跳自然停止，dashboard 就能看出进程卡住。"""
    while True:
        await _beat_once(factory)
        await asyncio.sleep(PROCESS_HEARTBEAT_INTERVAL)


def _build_media_store() -> tuple[MediaStore, Path]:
    """按配置构造存储和临时目录；缺配置时这里抛校验错误，进程启动失败（AC-20）。"""
    settings = load_media_settings("collector")
    if isinstance(settings, LocalMediaSettings):
        return build_local_store(settings.media_local_dir), settings.media_tmp_dir
    client = build_r2_client(
        settings.endpoint_url,
        settings.media_r2_write_access_key_id.get_secret_value(),
        settings.media_r2_write_secret_access_key.get_secret_value(),
    )
    return R2Store(client=client, bucket=settings.media_r2_bucket), settings.media_tmp_dir


async def _release_media_claims(factory: async_sessionmaker[AsyncSession]) -> None:
    """停止时尽力把正在下载的行放回队列；做不到也没关系，下次启动会释放（AC-18）。"""
    try:
        async with asyncio.timeout(2.0), factory() as session, session.begin():
            await media_db.release_claims_on_start(session)
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        log.warning("collector.media_release_failed", error_type=type(error).__name__)


async def _cancel_and_wait(task: "asyncio.Task[None]", limit: float) -> None:
    task.cancel()
    await asyncio.wait((task,), timeout=limit)


async def run() -> int:
    lock_conn = await _acquire_lock()
    if lock_conn is None:
        log.error("collector.lock_held", lock_key=raw.COLLECTOR_LOCK_KEY)
        return EXIT_LOCK_HELD
    log.info("collector.lock_acquired", lock_key=raw.COLLECTOR_LOCK_KEY)

    lost = asyncio.Event()
    lock_conn.add_termination_listener(lambda _conn: lost.set())

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    wake = asyncio.Event()
    channels_wake = asyncio.Event()
    media_wake = asyncio.Event()
    history_wake = asyncio.Event()

    # 复用锁连接：断开即退出。先 LISTEN 再第一次读账号，不会漏通知。
    def on_account_changed(_conn: object, _pid: int, _channel: str, _payload: object) -> None:
        wake.set()

    def on_collect_changed(_conn: object, _pid: int, _channel: str, _payload: object) -> None:
        channels_wake.set()
        media_wake.set()  # 频道重新打开后，暂停的附件马上继续（AC-23）

    def on_media_queue(_conn: object, _pid: int, _channel: str, _payload: object) -> None:
        media_wake.set()

    def on_history_changed(_conn: object, _pid: int, _channel: str, _payload: object) -> None:
        history_wake.set()

    await lock_conn.add_listener(raw.ACCOUNT_CHANGED_CHANNEL, on_account_changed)
    await lock_conn.add_listener(raw.CHANNEL_COLLECT_CHANGED_CHANNEL, on_collect_changed)
    await lock_conn.add_listener(raw.MEDIA_QUEUE_CHANNEL, on_media_queue)
    await lock_conn.add_listener(raw.HISTORY_CHANGED_CHANNEL, on_history_changed)

    engine = get_engine()
    media_http = media_worker.new_http_client()
    try:
        async with engine.connect() as conn:
            version = await conn.scalar(text("SHOW server_version"))
        log.info("collector.db_connected", server_version=version)

        guard = WakeGuard()
        factory = get_sessionmaker()
        store, tmp_dir = _build_media_store()
        await media_worker.prepare_start(factory, tmp_dir)
        gateway = GatewayHandle()
        media_deps = media_worker.MediaDeps(
            factory=factory,
            store=store,
            http=media_http,
            tmp_dir=tmp_dir,
            wake=media_wake,
            pause=media_worker.PauseState(),
            clock=_utc_now,
            gateway=gateway,
        )
        # 429 只从 `discord.http` 的警告日志里数；
        # 钩子收不到记录时先把历史采集关掉（spec 0012 AC-7）。
        ratelimit = RateLimitLog()
        install_ratelimit_watch(ratelimit, _utc_now)
        await history_worker.check_ratelimit_watch(factory)
        history_deps = history_worker.HistoryDeps(
            factory=factory,
            gateway=gateway,
            wake=history_wake,
            clock=_utc_now,
            uniform=random.uniform,
            ratelimit=ratelimit,
        )
        supervisor = asyncio.create_task(
            run_supervisor(
                Deps(
                    factory=factory,
                    key_ring=get_crypto_settings().key_ring,
                    new_session=partial(new_client, guard=guard),
                    clock=_utc_now,
                    uniform=random.uniform,
                    wake=wake,
                    channels_wake=channels_wake,
                    gateway=gateway,
                )
            ),
            name="supervisor",
        )
        background = (
            asyncio.create_task(_probe_lock(lock_conn, lost), name="lock-probe"),
            asyncio.create_task(_poll_account(wake), name="account-poll"),
            asyncio.create_task(watch_sleep(guard), name="sleep-watch"),
            asyncio.create_task(_beat_process(factory), name="process-heartbeat"),
            # 下载任务不放进 waiters：它出错只记日志、自己重试，不会让进程退出（AC-22）。
            asyncio.create_task(media_worker.run_download_loop(media_deps), name="media-worker"),
            asyncio.create_task(media_worker.run_heartbeat_loop(media_deps), name="media-beat"),
            asyncio.create_task(media_worker.run_refresh_loop(media_deps), name="media-refresh"),
            # 历史任务同样不放进 waiters：出错只记日志、自己从头再来（spec 0012）。
            asyncio.create_task(
                history_worker.run_history_loop(history_deps), name="history-worker"
            ),
            asyncio.create_task(
                history_worker.run_heartbeat_loop(history_deps), name="history-beat"
            ),
            asyncio.create_task(
                history_worker.run_cleanup_loop(history_deps), name="history-cleanup"
            ),
        )
        stop_wait = asyncio.create_task(stop.wait(), name="stop-wait")
        lost_wait = asyncio.create_task(lost.wait(), name="lost-wait")
        waiters = (supervisor, stop_wait, lost_wait)
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if lost.is_set():
                log.error("collector.lock_lost")
                await _cancel_and_wait(supervisor, LOCK_LOST_TIMEOUT)
                return EXIT_LOCK_LOST
            if stop.is_set():
                log.info("collector.stopping")
                await _cancel_and_wait(supervisor, SHUTDOWN_TIMEOUT)
                return EXIT_OK
            # 监督循环只有出错才会结束。异常内容可能带敏感信息，只记类型。
            error = supervisor.exception()
            log.error("collector.crashed", error_type=type(error).__name__)
            return EXIT_CRASH
        finally:
            for task in (*background, stop_wait, lost_wait, supervisor):
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*background, return_exceptions=True)
            await _release_media_claims(factory)
            await history_worker.release_on_stop(factory)
    finally:
        await media_http.aclose()
        await engine.dispose()
        if not lock_conn.is_closed():
            await lock_conn.close()
