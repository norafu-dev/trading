"""搜索文字的写入（spec 0013 AC-20）：只依赖模型，让 ingest 可以引用而不产生循环导入。"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.models import Attachment, Message
from harvester.db.search_text import build_search_text


async def refresh_search_text(session: AsyncSession, message_id: int) -> None:
    """按这条消息当前的内容和全部附件文件名重算 search_text（同一个事务里调用，AC-20）。"""
    row = (
        await session.execute(
            select(Message.content, Message.forwarded, Message.embeds).where(
                Message.id == message_id
            )
        )
    ).one_or_none()
    if row is None:
        return
    filenames = list(
        await session.scalars(
            select(Attachment.filename)
            .where(Attachment.message_id == message_id)
            .order_by(Attachment.id)
        )
    )
    value = build_search_text(row.content, row.forwarded, row.embeds, filenames)
    await session.execute(
        update(Message)
        .where(Message.id == message_id, Message.search_text.is_distinct_from(value))
        .values(search_text=value)
    )
