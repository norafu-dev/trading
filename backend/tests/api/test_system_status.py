"""spec 0009 AC-4、AC-7、AC-16：系统状态接口。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.system import record_collector_heartbeat
from tests.api.conftest import FakeClock, complete_setup, do_login, make_client

Factory = async_sessionmaker[AsyncSession]
URL = "/api/system/status"


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


async def test_requires_login(client: httpx.AsyncClient) -> None:  # covers: AC-16
    response = await client.get(URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_empty_state(logged_in: httpx.AsyncClient) -> None:  # covers: AC-4, AC-7
    body = (await logged_in.get(URL)).json()
    assert body == {
        "collector": {"last_heartbeat_at": None, "stale": False, "has_account": False},
        "backup": {"last_run": None, "last_success_at": None, "overdue": False},
        "media": {
            "storage_full": False,
            "last_seen_at": None,
            "stale": False,
            "paused_until": None,
        },
        "history": {
            "paused_reason": None,
            "paused_at": None,
            "waiting_reason": None,
            "waiting_until": None,
            "pages_used_24h": 0,
            "daily_page_limit": 1500,
            "quota_resumes_at": None,
            "last_seen_at": None,
            "stale": False,
            "gap_channel_count": 0,
        },
    }


async def test_reports_heartbeat_and_latest_backup(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-4, AC-7
    async with factory() as session, session.begin():
        await record_collector_heartbeat(session)
        await session.execute(
            text(
                "INSERT INTO backup_run (started_at, status, finished_at, size_bytes, object_key)"
                " VALUES (now() - interval '1 hour', 'succeeded', now(), 12345678,"
                " 'harvester-20261102T040312Z.dump.age')"
            )
        )

    body = (await logged_in.get(URL)).json()
    assert body["collector"]["stale"] is False
    assert body["collector"]["last_heartbeat_at"] is not None
    run = body["backup"]["last_run"]
    assert run["status"] == "succeeded"
    assert run["size_bytes"] == 12345678
    assert run["object_key"] == "harvester-20261102T040312Z.dump.age"
    assert run["error"] is None
    assert body["backup"]["overdue"] is False
    # 不返回 id、服务器路径或桶名。
    assert set(run) == {
        "started_at",
        "finished_at",
        "status",
        "size_bytes",
        "object_key",
        "error",
    }
