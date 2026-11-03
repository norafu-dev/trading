"""系统状态（spec 0009）：collector 进程心跳和备份记录。

心跳由 collector 写，备份记录由 `deploy/backup.sh` 写，api 只读。所有时间判断都用数据库时钟。
这里的函数都不提交事务。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.account import COLLECTOR_STALE_AFTER
from harvester.db.models import BackupRun, CollectorHeartbeat, DiscordAccount
from harvester.db.snapshots import BackupError, BackupStatus

BACKUP_OVERDUE_AFTER = timedelta(hours=48)


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
class SystemStatus:
    collector: CollectorState
    backup: BackupState


def is_process_stale(last_seen_at: datetime | None, db_now: datetime, *, has_account: bool) -> bool:
    """没有心跳时：有账号就算无响应（进程该在却没来过），没账号就是还没运行。"""
    if last_seen_at is None:
        return has_account
    return db_now - last_seen_at > COLLECTOR_STALE_AFTER


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


async def get_system_status(session: AsyncSession) -> SystemStatus:
    heartbeat = select(CollectorHeartbeat.last_seen_at).scalar_subquery()
    has_account = exists(select(DiscordAccount.id))
    last_success = (
        select(func.max(BackupRun.finished_at))
        .where(BackupRun.status == "succeeded")
        .scalar_subquery()
    )
    last_seen_at, account_exists, last_success_at, db_now = (
        await session.execute(select(heartbeat, has_account, last_success, func.now()))
    ).one()
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
            stale=is_process_stale(last_seen_at, db_now, has_account=account_exists),
            has_account=account_exists,
        ),
        backup=BackupState(
            last_run=last_run,
            last_success_at=last_success_at,
            overdue=is_backup_overdue(
                has_runs=last_run is not None, last_success_at=last_success_at, db_now=db_now
            ),
        ),
    )
