"""api 读 media 的查询（spec 0011）。这里的函数都不提交事务。"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.models import Media, StoredFile


@dataclass(frozen=True)
class StoredTarget:
    filename: str
    content_type: str
    object_key: str
    thumb_object_key: str | None


async def get_stored_target(session: AsyncSession, media_id: int) -> StoredTarget | None:
    """只有已保存（`stored`）的行才能读；其他状态按不存在处理。"""
    row = (
        await session.execute(
            select(
                Media.filename,
                StoredFile.content_type,
                StoredFile.object_key,
                StoredFile.thumb_object_key,
            )
            .join(StoredFile, StoredFile.id == Media.file_id)
            .where(Media.id == media_id, Media.status == "stored")
        )
    ).one_or_none()
    if row is None:
        return None
    return StoredTarget(row.filename, row.content_type, row.object_key, row.thumb_object_key)
