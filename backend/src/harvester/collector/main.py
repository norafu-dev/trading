import asyncio
import signal

import asyncpg
import structlog
from sqlalchemy import text

from harvester.db import get_engine, raw

log = structlog.get_logger("harvester.collector")

# How often the dedicated lock connection is probed while idle.
LOCK_CHECK_INTERVAL = 10.0

EXIT_OK = 0
EXIT_LOCK_HELD = 3
EXIT_LOCK_LOST = 4


async def _acquire_lock() -> asyncpg.Connection | None:
    """Take the session level advisory lock on a dedicated connection, or None if held."""
    conn = await raw.connect()
    acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", raw.COLLECTOR_LOCK_KEY)
    if not acquired:
        await conn.close()
        return None
    return conn


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

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            version = await conn.scalar(text("SHOW server_version"))
        log.info("collector.db_connected", server_version=version)

        # No token yet: Discord login arrives with feature 6. Idle until stopped or the lock drops.
        log.info("collector.idle", reason="no_account_configured")
        while not stop.is_set():
            try:
                await lock_conn.fetchval("SELECT 1")
            except (asyncpg.PostgresError, OSError, asyncpg.InterfaceError):
                lost.set()
            if lost.is_set():
                log.error("collector.lock_lost")
                return EXIT_LOCK_LOST
            try:
                await asyncio.wait_for(stop.wait(), timeout=LOCK_CHECK_INTERVAL)
            except TimeoutError:
                pass
        log.info("collector.stopping")
        return EXIT_OK
    finally:
        await engine.dispose()
        if not lock_conn.is_closed():
            await lock_conn.close()
