"""系统状态接口（spec 0009）：collector 进程心跳和最近一次备份，只读。"""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from harvester.db import SessionFactoryDep
from harvester.db import system as system_db
from harvester.db.snapshots import BackupError, BackupStatus

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承（AC-16）。
system_router = APIRouter(prefix="/system", tags=["system"])


class CollectorStatusOut(BaseModel):
    last_heartbeat_at: datetime | None
    stale: bool
    has_account: bool


class BackupRunOut(BaseModel):
    # 只给你在 R2 里找文件用的文件名；不返回服务器路径、桶名或密钥。
    started_at: datetime
    finished_at: datetime | None
    status: BackupStatus
    size_bytes: int | None
    object_key: str | None
    error: BackupError | None


class BackupStatusOut(BaseModel):
    last_run: BackupRunOut | None
    last_success_at: datetime | None
    overdue: bool


class SystemStatusOut(BaseModel):
    collector: CollectorStatusOut
    backup: BackupStatusOut


def _to_out(status: system_db.SystemStatus) -> SystemStatusOut:
    run = status.backup.last_run
    return SystemStatusOut(
        collector=CollectorStatusOut(
            last_heartbeat_at=status.collector.last_heartbeat_at,
            stale=status.collector.stale,
            has_account=status.collector.has_account,
        ),
        backup=BackupStatusOut(
            last_run=BackupRunOut(
                started_at=run.started_at,
                finished_at=run.finished_at,
                status=run.status,
                size_bytes=run.size_bytes,
                object_key=run.object_key,
                error=run.error,
            )
            if run is not None
            else None,
            last_success_at=status.backup.last_success_at,
            overdue=status.backup.overdue,
        ),
    )


@system_router.get("/status")
async def get_system_status(factory: SessionFactoryDep) -> SystemStatusOut:
    async with factory() as session:
        return _to_out(await system_db.get_system_status(session))
