"""系统状态（spec 0009）：collector 进程心跳和备份记录。

心跳由 collector 写，备份记录由 `deploy/backup.sh` 写，api 只读。所有时间判断都用数据库时钟。
这里的函数都不提交事务。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import history as history_db
from harvester.db.account import COLLECTOR_STALE_AFTER
from harvester.db.history_ranges import quota_resumes_at
from harvester.db.models import BackupRun, CollectorHeartbeat, DiscordAccount, Media
from harvester.db.snapshots import (
    BackupError,
    BackupStatus,
    HistoryPausedReason,
    HistoryWaitingReason,
)

BACKUP_OVERDUE_AFTER = timedelta(hours=48)
# 进程心跳正常而下载任务心跳超过这么久没更新，就算无响应（spec 0011 AC-22）。
MEDIA_WORKER_STALE_AFTER = timedelta(seconds=180)
# 历史任务心跳同理（spec 0012 AC-20）。
HISTORY_WORKER_STALE_AFTER = timedelta(seconds=180)


@dataclass(frozen=True)
class BackupRunRow:
    started_at: datetime
    finished_at: datetime | None
    status: BackupStatus
    size_bytes: int | None
    object_key: str | None
    error: BackupError | None


@dataclass(frozen=True)
class CollectorState:
    last_heartbeat_at: datetime | None
    stale: bool
    has_account: bool


@dataclass(frozen=True)
class BackupState:
    last_run: BackupRunRow | None
    last_success_at: datetime | None
    overdue: bool


@dataclass(frozen=True)
class MediaState:
    storage_full: bool
    last_seen_at: datetime | None
    stale: bool
    paused_until: datetime | None


@dataclass(frozen=True)
class HistoryState:
    paused_reason: HistoryPausedReason | None
    paused_at: datetime | None
    waiting_reason: HistoryWaitingReason | None
    waiting_until: datetime | None
    pages_used_24h: int
    daily_page_limit: int
    quota_resumes_at: datetime | None
    last_seen_at: datetime | None
    stale: bool


@dataclass(frozen=True)
class SystemStatus:
    collector: CollectorState
    backup: BackupState
    media: MediaState
    history: HistoryState


def is_process_stale(last_seen_at: datetime | None, db_now: datetime, *, has_account: bool) -> bool:
    """没有心跳时：有账号就算无响应（进程该在却没来过），没账号就是还没运行。"""
    if last_seen_at is None:
        return has_account
    return db_now - last_seen_at > COLLECTOR_STALE_AFTER


def is_media_worker_stale(
    media_seen_at: datetime | None, db_now: datetime, *, process_alive: bool
) -> bool:
    """只有 collector 进程本身正常时才报下载任务无响应；进程挂了由进程那一行报。"""
    if not process_alive:
        return False
    return media_seen_at is None or db_now - media_seen_at > MEDIA_WORKER_STALE_AFTER


def is_history_worker_stale(
    history_seen_at: datetime | None, db_now: datetime, *, process_alive: bool
) -> bool:
    """只有 collector 进程本身正常时才报历史任务无响应（AC-20）。"""
    if not process_alive:
        return False
    return history_seen_at is None or db_now - history_seen_at > HISTORY_WORKER_STALE_AFTER


def is_backup_overdue(
    *, has_runs: bool, last_success_at: datetime | None, db_now: datetime
) -> bool:
    """一条记录都没有时不告警；有记录但从没成功或最近成功太久，才算逾期。"""
    if not has_runs:
        return False
    return last_success_at is None or db_now - last_success_at > BACKUP_OVERDUE_AFTER


async def record_collector_heartbeat(session: AsyncSession) -> None:
    await session.execute(
        pg_insert(CollectorHeartbeat)
        .values(id=True, last_seen_at=func.now())
        .on_conflict_do_update(
            index_elements=[CollectorHeartbeat.id], set_={"last_seen_at": func.now()}
        )
    )


async def get_history_state(session: AsyncSession) -> HistoryState:
    """历史采集的总开关、整体等待、用量和心跳（AC-6、AC-19、AC-20），时间用数据库时钟。"""
    db_now: datetime = (await session.execute(select(func.now()))).scalar_one()
    settings = await history_db.get_history_settings(session)
    buckets = await history_db.pages_in_window(session, db_now)
    beat = (
        await session.execute(
            select(
                CollectorHeartbeat.last_seen_at,
                CollectorHeartbeat.history_worker_seen_at,
                CollectorHeartbeat.history_waiting_reason,
                CollectorHeartbeat.history_waiting_until,
            )
        )
    ).one_or_none()
    process_seen, history_seen, waiting_reason, waiting_until = beat or (None, None, None, None)
    has_account = bool(await session.scalar(select(exists(select(DiscordAccount.id)))))
    process_alive = process_seen is not None and not is_process_stale(
        process_seen, db_now, has_account=has_account
    )
    waiting = waiting_until is not None and waiting_until > db_now
    used = sum(pages for _, pages in buckets)
    return HistoryState(
        paused_reason=settings.paused_reason,
        paused_at=settings.paused_at,
        waiting_reason=waiting_reason if waiting else None,
        waiting_until=waiting_until if waiting else None,
        pages_used_24h=used,
        daily_page_limit=settings.daily_page_limit,
        quota_resumes_at=quota_resumes_at(buckets, settings.daily_page_limit),
        last_seen_at=history_seen,
        stale=is_history_worker_stale(history_seen, db_now, process_alive=process_alive),
    )


async def get_system_status(session: AsyncSession) -> SystemStatus:
    heartbeat = select(CollectorHeartbeat.last_seen_at).scalar_subquery()
    media_seen = select(CollectorHeartbeat.media_worker_seen_at).scalar_subquery()
    media_paused = select(CollectorHeartbeat.media_paused_until).scalar_subquery()
    storage_full = exists(select(Media.id).where(Media.status == "no_space"))
    has_account = exists(select(DiscordAccount.id))
    last_success = (
        select(func.max(BackupRun.finished_at))
        .where(BackupRun.status == "succeeded")
        .scalar_subquery()
    )
    last_seen_at, account_exists, last_success_at, db_now, seen, paused, full = (
        await session.execute(
            select(
                heartbeat,
                has_account,
                last_success,
                func.now(),
                media_seen,
                media_paused,
                storage_full,
            )
        )
    ).one()
    process_stale = is_process_stale(last_seen_at, db_now, has_account=account_exists)
    paused_until: datetime | None = paused
    latest = await session.scalar(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(1))
    last_run = (
        BackupRunRow(
            started_at=latest.started_at,
            finished_at=latest.finished_at,
            status=latest.status,
            size_bytes=latest.size_bytes,
            object_key=latest.object_key,
            error=latest.error,
        )
        if latest is not None
        else None
    )
    return SystemStatus(
        collector=CollectorState(
            last_heartbeat_at=last_seen_at,
            stale=process_stale,
            has_account=account_exists,
        ),
        backup=BackupState(
            last_run=last_run,
            last_success_at=last_success_at,
            overdue=is_backup_overdue(
                has_runs=last_run is not None, last_success_at=last_success_at, db_now=db_now
            ),
        ),
        media=MediaState(
            storage_full=bool(full),
            last_seen_at=seen,
            stale=is_media_worker_stale(
                seen, db_now, process_alive=bool(last_seen_at) and not process_stale
            ),
            paused_until=paused_until if paused_until and paused_until > db_now else None,
        ),
        history=await get_history_state(session),
    )


async def record_media_worker_heartbeat(
    session: AsyncSession, paused_until: datetime | None
) -> None:
    """下载任务的心跳和暂停结束时间（spec 0011）。进程心跳那一行还没建出来时什么也不写。"""
    await session.execute(
        update(CollectorHeartbeat)
        .where(CollectorHeartbeat.id)
        .values(media_worker_seen_at=func.now(), media_paused_until=paused_until)
    )
