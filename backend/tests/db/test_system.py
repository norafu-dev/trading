"""spec 0009：进程心跳、备份记录的约束和系统状态读取（真实 PostgreSQL）。"""

from datetime import timedelta

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db import system
from harvester.db.models import CollectorHeartbeat, DiscordAccount
from tests.db.builders import at

NOW = at(0)


def test_process_stale_rules() -> None:  # covers: AC-4
    assert not system.is_process_stale(None, NOW, has_account=False)
    assert system.is_process_stale(None, NOW, has_account=True)
    assert not system.is_process_stale(NOW - timedelta(seconds=120), NOW, has_account=True)
    assert system.is_process_stale(NOW - timedelta(seconds=121), NOW, has_account=False)


def test_backup_overdue_rules() -> None:  # covers: AC-7
    edge = NOW - system.BACKUP_OVERDUE_AFTER
    assert not system.is_backup_overdue(has_runs=False, last_success_at=None, db_now=NOW)
    assert system.is_backup_overdue(has_runs=True, last_success_at=None, db_now=NOW)
    assert not system.is_backup_overdue(has_runs=True, last_success_at=edge, db_now=NOW)
    assert system.is_backup_overdue(
        has_runs=True, last_success_at=edge - timedelta(seconds=1), db_now=NOW
    )


async def test_heartbeat_upserts_a_single_row(session: AsyncSession) -> None:  # covers: AC-3
    await system.record_collector_heartbeat(session)
    await session.commit()
    first = await session.scalar(select(CollectorHeartbeat.last_seen_at))
    await system.record_collector_heartbeat(session)
    await session.commit()

    rows = (await session.execute(select(CollectorHeartbeat))).scalars().all()
    assert len(rows) == 1
    assert first is not None and rows[0].last_seen_at >= first


async def test_heartbeat_table_rejects_a_second_row(session: AsyncSession) -> None:  # covers: AC-3
    with pytest.raises(IntegrityError):
        await session.execute(insert(CollectorHeartbeat).values(id=False, last_seen_at=func.now()))


@pytest.mark.parametrize(
    "values",
    [
        pytest.param("'running', now(), NULL, NULL, NULL", id="running-with-finish"),
        pytest.param("'succeeded', now(), NULL, 'k', NULL", id="succeeded-without-size"),
        pytest.param("'succeeded', now(), 10, 'k', 'dump'", id="succeeded-with-error"),
        pytest.param("'failed', now(), NULL, NULL, NULL", id="failed-without-step"),
        pytest.param("'failed', now(), 10, NULL, 'upload'", id="failed-with-size"),
        pytest.param("'failed', now(), NULL, NULL, 'oops'", id="unknown-step"),
        pytest.param("'paused', NULL, NULL, NULL, NULL", id="unknown-status"),
    ],
)
async def test_backup_run_constraints(session: AsyncSession, values: str) -> None:  # covers: AC-6
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO backup_run (status, finished_at, size_bytes, object_key, error)"
                f" VALUES ({values})"
            )
        )


async def _run(session: AsyncSession, values: str, started: str = "now()") -> None:
    await session.execute(
        text(
            "INSERT INTO backup_run (started_at, status, finished_at, size_bytes, object_key,"
            f" error) VALUES ({started}, {values})"
        )
    )


async def test_status_with_nothing_recorded(session: AsyncSession) -> None:  # covers: AC-4, AC-7
    status = await system.get_system_status(session)

    assert status.collector == system.CollectorState(
        last_heartbeat_at=None, stale=False, has_account=False
    )
    assert status.backup == system.BackupState(last_run=None, last_success_at=None, overdue=False)


async def test_account_without_heartbeat_is_stale(session: AsyncSession) -> None:  # covers: AC-4
    await session.execute(
        insert(DiscordAccount).values(label="main", token_ciphertext=b"x", token_key_version=1)
    )

    collector = (await system.get_system_status(session)).collector
    assert (collector.has_account, collector.stale) == (True, True)


async def test_fresh_heartbeat_is_not_stale(session: AsyncSession) -> None:  # covers: AC-4
    await system.record_collector_heartbeat(session)

    collector = (await system.get_system_status(session)).collector
    assert collector.last_heartbeat_at is not None and not collector.stale


async def test_only_failed_runs_are_overdue(session: AsyncSession) -> None:  # covers: AC-7
    await _run(session, "'failed', now(), NULL, NULL, 'upload'")

    backup = (await system.get_system_status(session)).backup
    assert backup.last_run is not None and backup.last_run.error == "upload"
    assert backup.last_success_at is None and backup.overdue


async def test_only_running_is_overdue(session: AsyncSession) -> None:  # covers: AC-7
    await _run(session, "'running', NULL, NULL, NULL, NULL")

    backup = (await system.get_system_status(session)).backup
    assert backup.last_run is not None and backup.last_run.status == "running"
    assert backup.overdue


@pytest.mark.parametrize(
    ("age", "overdue"),
    [("interval '47 hours 59 minutes'", False), ("interval '48 hours 1 minute'", True)],
)
async def test_latest_success_decides_overdue(
    session: AsyncSession, age: str, overdue: bool
) -> None:  # covers: AC-7
    await _run(
        session, f"'succeeded', now() - {age}, 1234567, 'a.dump.age', NULL", f"now() - {age}"
    )
    # 最近一次失败不影响「最近一次成功」。
    await _run(session, "'failed', now(), NULL, NULL, 'dump'")

    backup = (await system.get_system_status(session)).backup
    assert backup.last_run is not None and backup.last_run.status == "failed"
    assert backup.last_success_at is not None
    assert backup.overdue is overdue
