"""api 对 media 的读写（spec 0011）。这里的函数都不提交事务。

api 只改 media 的状态（重试、改设置引起的重新排队）和 media_settings；文件和 stored_file
只有 collector 写。
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import media as media_db
from harvester.db.messages import MediaRow, media_by_message, notify_message_changed
from harvester.db.models import Channel, Media, MediaSettings, Message, StoredFile
from harvester.db.snapshots import MediaStatus

MB = 1024**2
GB = 1024**3
MAX_FILE_MB_RANGE = (1, 1024)
QUOTA_GB_RANGE = (1, 10000)
ALL_STATUSES: tuple[MediaStatus, ...] = (
    "pending",
    "stored",
    "failed",
    "too_large",
    "no_space",
    "gone",
)
RetryError = Literal["not_found", "not_retryable"]


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


@dataclass(frozen=True)
class MediaUsage:
    max_file_mb: int
    quota_gb: int
    used_bytes: int
    counts: dict[MediaStatus, int]
    pending_channel_off: int
    storage_full: bool


async def get_usage(session: AsyncSession) -> MediaUsage:
    limits = await media_db.get_limits(session)
    grouped = dict(
        (await session.execute(select(Media.status, func.count()).group_by(Media.status))).all()
    )
    off = await session.scalar(
        select(func.count())
        .select_from(Media)
        .join(Message, Message.id == Media.message_id)
        .join(Channel, Channel.id == Message.channel_id)
        .where(Media.status == "pending", Channel.collect_enabled.is_(False))
    )
    return MediaUsage(
        max_file_mb=limits.max_file_bytes // MB,
        quota_gb=limits.quota_bytes // GB,
        used_bytes=await media_db.used_bytes(session),
        counts={status: int(grouped.get(status, 0)) for status in ALL_STATUSES},
        pending_channel_off=int(off or 0),
        storage_full=bool(await session.scalar(select(exists().where(Media.status == "no_space")))),
    )


async def update_limits(session: AsyncSession, max_file_mb: int, quota_gb: int) -> MediaUsage:
    """写设置，并按 AC-9 把能放下了的行排回队列；范围由调用方（接口校验）保证。"""
    max_file = max_file_mb * MB
    quota = quota_gb * GB
    await session.execute(
        update(MediaSettings)
        .where(MediaSettings.id == 1)
        .values(max_file_bytes=max_file, quota_bytes=quota, updated_at=func.now())
    )
    requeue = {
        "status": "pending",
        "aborted_at_bytes": None,
        "last_error": None,
        "next_attempt_at": func.now(),
    }
    too_large = await session.scalars(
        update(Media)
        .where(
            Media.status == "too_large",
            (
                (Media.declared_size_bytes.is_not(None) & (Media.declared_size_bytes <= max_file))
                | (Media.declared_size_bytes.is_(None) & (Media.aborted_at_bytes < max_file))
            ),
        )
        .values(**requeue)
        .returning(Media.id)
    )
    changed = len(too_large.all())
    if await media_db.used_bytes(session) < quota:
        no_space = await session.scalars(
            update(Media).where(Media.status == "no_space").values(**requeue).returning(Media.id)
        )
        changed += len(no_space.all())
    if changed:
        await media_db.notify_media_queue(session)
    return await get_usage(session)


async def retry_one(session: AsyncSession, media_id: int) -> MediaRow | RetryError:
    """`failed` 和 `gone` 手动重试；`too_large` 和 `no_space` 只能靠改设置（AC-17）。"""
    status = await session.scalar(select(Media.status).where(Media.id == media_id))
    if status is None:
        return "not_found"
    if status not in ("failed", "gone"):
        return "not_retryable"
    row = (
        await session.execute(
            update(Media)
            .where(Media.id == media_id, Media.status.in_(("failed", "gone")))
            .values(
                status="pending",
                attempts=0,
                last_error=None,
                claimed_at=None,
                needs_refresh=False,
                refreshed_at=None,
                next_attempt_at=func.now(),
            )
            .returning(Media.message_id)
        )
    ).one_or_none()
    if row is None:  # 刚被别处改掉
        return "not_retryable"
    channel_id = await session.scalar(
        select(Message.channel_id).where(Message.id == row.message_id)
    )
    if channel_id is not None:
        await notify_message_changed(session, channel_id, (row.message_id,))
    await media_db.notify_media_queue(session)
    found = next(
        (
            m
            for m in (await media_by_message(session, (row.message_id,)))[row.message_id]
            if m.id == media_id
        ),
        None,
    )
    return found if found is not None else "not_found"


async def retry_all_failed(session: AsyncSession) -> int:
    """设置页的「重试全部失败」：只动 `failed`，不含 `gone`。"""
    rows = (
        await session.execute(
            update(Media)
            .where(Media.status == "failed")
            .values(
                status="pending",
                attempts=0,
                last_error=None,
                claimed_at=None,
                needs_refresh=False,
                refreshed_at=None,
                next_attempt_at=func.now(),
            )
            .returning(Media.message_id)
        )
    ).all()
    if rows:
        await media_db.notify_media_queue(session)
    return len(rows)
