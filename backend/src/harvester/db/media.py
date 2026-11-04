"""附件和 embed 图的下载队列（spec 0011）。这里的函数都不提交事务。

collector 的下载任务领取、写结果；api 只改状态（重试、改设置引起的重新排队）。
所有时间判断用数据库时钟，写结果的每一条 UPDATE 都带 `claimed_at` 比较：领取之后这一行
被重置或手动重试过，旧的下载结果就写不进去（AC-18）。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import Table, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.models import Media, MediaSettings, StoredFile
from harvester.db.raw import MEDIA_QUEUE_CHANNEL
from harvester.db.snapshots import (
    AttachmentSnapshot,
    EmbedData,
    MediaKind,
    MediaStatus,
    MessageSnapshot,
)
from harvester.media.urls import (
    discord_attachment_id,
    filename_from_url,
    is_discord_hosted,
    strip_query,
)

_MEDIA = cast(Table, Media.__table__)
_EMBED_FIELDS: tuple[tuple[MediaKind, str], ...] = (
    ("embed_image", "image_url"),
    ("embed_thumbnail", "thumbnail_url"),
)


@dataclass(frozen=True)
class MediaLimits:
    max_file_bytes: int
    quota_bytes: int


@dataclass(frozen=True)
class ClaimedMedia:
    """下载任务领取的一行，连同它所在消息的频道和时间（决定存储路径）。"""

    id: int
    message_id: int
    channel_id: int
    message_created_at: datetime
    kind: MediaKind
    filename: str
    declared_content_type: str | None
    declared_size_bytes: int | None
    declared_width: int | None
    declared_height: int | None
    source_url: str
    attempts: int
    refreshed_at: datetime | None
    # 领取时写入的 claimed_at：之后写结果时用它确认这次领取仍然有效。
    claimed_at: datetime
    db_now: datetime


@dataclass(frozen=True)
class NewStoredFile:
    prefix: str
    sha256: bytes
    size_bytes: int
    content_type: str
    object_key: str
    width: int | None = None
    height: int | None = None
    thumb_object_key: str | None = None
    thumb_size_bytes: int | None = None
    thumb_width: int | None = None
    thumb_height: int | None = None


async def notify_media_queue(session: AsyncSession) -> None:
    await session.execute(text("SELECT pg_notify(:channel, '')"), {"channel": MEDIA_QUEUE_CHANNEL})


# ---- 建行（ingest_message 的事务里调用）------------------------------------------------


def _attachment_values(message_id: int, kind: MediaKind, slot: int | None, a: AttachmentSnapshot):
    return {
        "message_id": message_id,
        "kind": kind,
        "attachment_id": a.id if kind == "attachment" else None,
        "slot": slot,
        "discord_attachment_id": a.id,
        "filename": a.filename,
        "declared_content_type": a.content_type,
        "declared_size_bytes": a.size_bytes,
        "declared_width": a.width,
        "declared_height": a.height,
        "source_url": a.url,
    }


async def _sync_attachment_rows(
    session: AsyncSession, message_id: int, attachments: Sequence[AttachmentSnapshot]
) -> bool:
    """每个附件一行；已有行只更新链接（新链接更新鲜），已保存的不动。返回有没有新行。"""
    if not attachments:
        return False
    stmt = insert(_MEDIA).values(
        [_attachment_values(message_id, "attachment", None, a) for a in attachments]
    )
    inserted = await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[_MEDIA.c.attachment_id],
            set_={"source_url": stmt.excluded.source_url},
            where=_MEDIA.c.status != "stored",
        ).returning(text("(xmax = 0) AS inserted"))
    )
    return any(row.inserted for row in inserted)


async def _sync_forwarded_rows(
    session: AsyncSession, message_id: int, attachments: Sequence[AttachmentSnapshot]
) -> bool:
    if not attachments:
        return False
    stmt = insert(_MEDIA).values(
        [
            _attachment_values(message_id, "forwarded_attachment", slot, a)
            for slot, a in enumerate(attachments)
        ]
    )
    inserted = await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[_MEDIA.c.message_id, _MEDIA.c.kind, _MEDIA.c.slot],
            index_where=text("kind <> 'attachment'"),
            set_={"source_url": stmt.excluded.source_url},
            where=_MEDIA.c.status != "stored",
        ).returning(text("(xmax = 0) AS inserted"))
    )
    return any(row.inserted for row in inserted)


# 换图：去掉查询参数后的地址不同。一条 UPDATE 把所有会让 CHECK 失败的列一起清掉（AC-13）。
_RESET_VALUES: Mapping[str, object] = {
    "status": "pending",
    "file_id": None,
    "stored_at": None,
    "last_error": None,
    "claimed_at": None,
    "refreshed_at": None,
    "aborted_at_bytes": None,
    "attempts": 0,
    "needs_refresh": False,
    "next_attempt_at": func.now(),
}


async def _sync_embed_rows(
    session: AsyncSession, message_id: int, embeds: Sequence[EmbedData] | None
) -> bool:
    """Discord 托管的 embed 图建行；换了图的重置；第三方图片不建行。返回有没有新建或重置。"""
    wanted: list[tuple[MediaKind, int, str]] = []
    for slot, embed in enumerate(embeds or ()):
        for kind, field in _EMBED_FIELDS:
            url = embed.get(field)
            if isinstance(url, str) and is_discord_hosted(url):
                wanted.append((kind, slot, url))
    if not wanted:
        return False
    existing = {
        (row.kind, row.slot): row
        for row in await session.execute(
            select(Media.id, Media.kind, Media.slot, Media.source_url, Media.status).where(
                Media.message_id == message_id, Media.kind != "attachment"
            )
        )
    }
    changed = False
    for kind, slot, url in wanted:
        row = existing.get((kind, slot))
        if row is None:
            await session.execute(
                insert(_MEDIA)
                .values(
                    message_id=message_id,
                    kind=kind,
                    slot=slot,
                    discord_attachment_id=discord_attachment_id(url),
                    filename=filename_from_url(url),
                    source_url=url,
                )
                .on_conflict_do_nothing()
            )
            changed = True
        elif strip_query(row.source_url) != strip_query(url):
            await session.execute(
                update(Media)
                .where(Media.id == row.id)
                .values(
                    **_RESET_VALUES,
                    source_url=url,
                    filename=filename_from_url(url),
                    discord_attachment_id=discord_attachment_id(url),
                )
            )
            changed = True
        elif row.status != "stored":
            await session.execute(update(Media).where(Media.id == row.id).values(source_url=url))
    return changed


async def sync_media_for_message(
    session: AsyncSession, snap: MessageSnapshot, embeds: Sequence[EmbedData] | None
) -> None:
    """用快照（不是库里的行）决定要建什么；每次入库都同步，所以实时和补采并发也不会漏行。

    附件被拿掉、embed 被拿掉都不动已有的行（AC-13）。有新建或重置时在同一个事务里通知。
    """
    changed = False
    if snap.attachments_known:
        changed |= await _sync_attachment_rows(session, snap.id, snap.attachments)
        changed |= await _sync_forwarded_rows(session, snap.id, snap.forwarded_attachments)
    changed |= await _sync_embed_rows(session, snap.id, embeds)
    if changed:
        await notify_media_queue(session)


# ---- 下载任务 ------------------------------------------------------------------------

# 只领取所在频道开着采集的行（AC-23）；新消息在前，补下载从最近往回（AC-12）。
_CLAIM_SQL = text(
    """
    WITH picked AS (
        SELECT media.id
        FROM media
        JOIN message ON message.id = media.message_id
        JOIN channel ON channel.id = message.channel_id
        WHERE media.status = 'pending'
          AND media.claimed_at IS NULL
          AND NOT media.needs_refresh
          AND media.next_attempt_at <= now()
          AND channel.collect_enabled
        ORDER BY media.message_id DESC, media.id
        LIMIT :n
        FOR UPDATE OF media SKIP LOCKED
    )
    UPDATE media SET claimed_at = clock_timestamp()
    FROM picked, message
    WHERE media.id = picked.id AND message.id = media.message_id
    RETURNING media.id, media.message_id, message.channel_id,
              message.created_at AS message_created_at, media.kind, media.filename,
              media.declared_content_type, media.declared_size_bytes, media.declared_width,
              media.declared_height, media.source_url, media.attempts, media.refreshed_at,
              media.claimed_at, now() AS db_now
    """
)


async def claim_batch(session: AsyncSession, limit: int) -> tuple[ClaimedMedia, ...]:
    if limit <= 0:
        return ()
    rows = (await session.execute(_CLAIM_SQL, {"n": limit})).all()
    claimed = tuple(
        ClaimedMedia(
            id=r.id,
            message_id=r.message_id,
            channel_id=r.channel_id,
            message_created_at=r.message_created_at,
            kind=r.kind,
            filename=r.filename,
            declared_content_type=r.declared_content_type,
            declared_size_bytes=r.declared_size_bytes,
            declared_width=r.declared_width,
            declared_height=r.declared_height,
            source_url=r.source_url,
            attempts=r.attempts,
            refreshed_at=r.refreshed_at,
            claimed_at=r.claimed_at,
            db_now=r.db_now,
        )
        for r in rows
    )
    return tuple(sorted(claimed, key=lambda c: (-c.message_id, c.id)))


async def release_claims_on_start(session: AsyncSession) -> int:
    """collector 有单实例锁，启动时不可能有别的进程在处理：领取标记全部放回队列（AC-18）。"""
    result = await session.execute(
        update(Media)
        .where(Media.claimed_at.is_not(None))
        .values(claimed_at=None, next_attempt_at=func.now())
        .returning(Media.id)
    )
    return len(result.all())


async def release_claims(session: AsyncSession, claims: Sequence[tuple[int, datetime]]) -> None:
    """停止时尽力放回本进程领取的行；已经被重置的（claimed_at 变了）不受影响。"""
    for media_id, claimed_at in claims:
        await session.execute(
            update(Media)
            .where(Media.id == media_id, Media.claimed_at == claimed_at)
            .values(claimed_at=None, next_attempt_at=func.now())
        )


async def get_limits(session: AsyncSession) -> MediaLimits:
    row = (
        await session.execute(select(MediaSettings.max_file_bytes, MediaSettings.quota_bytes))
    ).one()
    return MediaLimits(max_file_bytes=row.max_file_bytes, quota_bytes=row.quota_bytes)


async def used_bytes(session: AsyncSession) -> int:
    """所有 stored_file 的原文件加缩略图（AC-7）。"""
    total = await session.scalar(
        select(
            func.coalesce(
                func.sum(StoredFile.size_bytes + func.coalesce(StoredFile.thumb_size_bytes, 0)), 0
            )
        )
    )
    return int(total or 0)


async def find_stored_file_id(session: AsyncSession, prefix: str, sha256: bytes) -> int | None:
    return await session.scalar(
        select(StoredFile.id).where(StoredFile.prefix == prefix, StoredFile.sha256 == sha256)
    )


async def _insert_stored_file(session: AsyncSession, new: NewStoredFile) -> int:
    await session.execute(
        insert(StoredFile)
        .values(
            prefix=new.prefix,
            sha256=new.sha256,
            size_bytes=new.size_bytes,
            content_type=new.content_type,
            object_key=new.object_key,
            width=new.width,
            height=new.height,
            thumb_object_key=new.thumb_object_key,
            thumb_size_bytes=new.thumb_size_bytes,
            thumb_width=new.thumb_width,
            thumb_height=new.thumb_height,
        )
        .on_conflict_do_nothing(constraint="uq_stored_file_prefix_sha256")
    )
    file_id = await find_stored_file_id(session, new.prefix, new.sha256)
    if file_id is None:  # 刚插入或已存在，读不到说明有人删了行，这是意外。
        raise RuntimeError("stored_file row vanished after insert")
    return file_id


async def record_stored(
    session: AsyncSession,
    media_id: int,
    claimed_at: datetime,
    *,
    file: NewStoredFile | None,
    existing_file_id: int | None,
) -> bool:
    """写 stored。`False` 表示领取已失效（被重置或手动重试），结果丢弃。"""
    file_id = existing_file_id if file is None else await _insert_stored_file(session, file)
    if file_id is None:
        raise ValueError("record_stored needs a file or an existing file id")
    updated = await session.scalar(
        update(Media)
        .where(Media.id == media_id, Media.claimed_at == claimed_at)
        .values(
            status="stored",
            file_id=file_id,
            stored_at=func.now(),
            claimed_at=None,
            needs_refresh=False,
            last_error=None,
        )
        .returning(Media.id)
    )
    return updated is not None


async def record_retry(
    session: AsyncSession,
    media_id: int,
    claimed_at: datetime,
    *,
    attempts: int,
    backoff_seconds: float,
    final: bool,
    error: str,
) -> bool:
    """可以重试的失败：次数加一，退避；`final` 为真（第 5 次）时记为 failed。"""
    updated = await session.scalar(
        update(Media)
        .where(Media.id == media_id, Media.claimed_at == claimed_at)
        .values(
            status="failed" if final else "pending",
            attempts=attempts,
            next_attempt_at=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, backoff_seconds),
            claimed_at=None,
            last_error=error,
        )
        .returning(Media.id)
    )
    return updated is not None


async def record_terminal(
    session: AsyncSession,
    media_id: int,
    claimed_at: datetime,
    status: MediaStatus,
    *,
    error: str | None,
    aborted_at_bytes: int | None = None,
) -> bool:
    updated = await session.scalar(
        update(Media)
        .where(Media.id == media_id, Media.claimed_at == claimed_at)
        .values(
            status=status,
            claimed_at=None,
            last_error=error,
            aborted_at_bytes=aborted_at_bytes,
            needs_refresh=False,
        )
        .returning(Media.id)
    )
    return updated is not None


async def mark_needs_refresh(session: AsyncSession, media_id: int, claimed_at: datetime) -> bool:
    """链接快过期或下载得到 403、404：交给换链接循环，不计入 attempts。"""
    updated = await session.scalar(
        update(Media)
        .where(Media.id == media_id, Media.claimed_at == claimed_at)
        .values(needs_refresh=True, claimed_at=None)
        .returning(Media.id)
    )
    return updated is not None
