"""已有消息的 search_text 后台补建（spec 0013 AC-22）。

api 只有一个 worker，所以只有一个补建循环。取批用 SKIP LOCKED、写入只写 NULL 的行，
和 collector 同时写同一条消息也不会互相覆盖。
"""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.models import Attachment, Message
from harvester.db.search_text import build_search_text

log = structlog.get_logger("harvester.search_backfill")

BATCH_SIZE = 1000
BATCH_PAUSE_S = 0.2
RECHECK_S = 600.0
ERROR_PAUSE_S = 30.0
PROGRESS_EVERY = 50

Sleep = Callable[[float], Awaitable[None]]


async def backfill_batch(session: AsyncSession, size: int = BATCH_SIZE) -> int:
    """补一批（最新的先补），返回这批的条数。调用方负责事务。"""
    ids = list(
        await session.scalars(
            select(Message.id)
            .where(Message.search_text.is_(None))
            .order_by(Message.id.desc())
            .limit(size)
            .with_for_update(skip_locked=True)
        )
    )
    if not ids:
        return 0
    rows = (
        await session.execute(
            select(Message.id, Message.content, Message.forwarded, Message.embeds).where(
                Message.id.in_(ids)
            )
        )
    ).all()
    filenames: defaultdict[int, list[str]] = defaultdict(list)
    attachment_rows = await session.execute(
        select(Attachment.message_id, Attachment.filename)
        .where(Attachment.message_id.in_(ids))
        .order_by(Attachment.id)
    )
    for message_id, filename in attachment_rows:
        filenames[message_id].append(filename)
    for row in rows:
        value = build_search_text(row.content, row.forwarded, row.embeds, filenames[row.id])
        await session.execute(
            update(Message)
            .where(Message.id == row.id, Message.search_text.is_(None))
            .values(search_text=value)
        )
    return len(ids)


async def _analyze(factory: async_sessionmaker[AsyncSession]) -> None:
    """让计划器拿到补建后的统计；ANALYZE 可以在事务里跑。"""
    async with factory() as session, session.begin():
        await session.execute(text("ANALYZE message"))


async def run_search_backfill(
    factory: async_sessionmaker[AsyncSession], sleep: Sleep = asyncio.sleep
) -> None:
    """一直跑到被取消：补完后每 10 分钟再查一次漏网的。"""
    processed = 0
    batches = 0
    while True:
        try:
            async with factory() as session, session.begin():
                count = await backfill_batch(session)
            if count:
                processed += count
                batches += 1
                if batches % PROGRESS_EVERY == 0:
                    log.info("search_backfill.progress", processed=processed)
                await sleep(BATCH_PAUSE_S)
                continue
            if processed:
                log.info("search_backfill.done", processed=processed)
                await _analyze(factory)
                processed = 0
                batches = 0
            await sleep(RECHECK_S)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("search_backfill.error", error_type=type(error).__name__)
            await sleep(ERROR_PAUSE_S)
