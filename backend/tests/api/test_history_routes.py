"""spec 0012：建任务和任务列表接口（真实 PostgreSQL）。"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.history import (
    record_history_heartbeat,
    record_pages,
    set_history_paused,
    set_history_waiting,
)
from harvester.db.models import AccountChannelAccess, HistoryJob
from harvester.db.system import record_collector_heartbeat
from tests.api.conftest import FakeClock, complete_setup, do_login, make_client
from tests.db.builders import CHANNEL, make_account, seed_channel

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


@pytest.fixture
async def readable(factory: Factory) -> None:
    async with factory() as session, session.begin():
        await seed_channel(session)
        account = await make_account(session)
        session.add(AccountChannelAccess(account_id=account, channel_id=CHANNEL.id, can_read=True))


BODY = {
    "channel_ids": [str(CHANNEL.id)],
    "range_start": "2026-01-01T00:00:00+08:00",
    "range_end": "2026-01-02T00:00:00+08:00",
}


async def test_create_and_list_jobs(logged_in: httpx.AsyncClient, readable: None) -> None:
    created = await logged_in.post(
        "/api/history/jobs", json={**BODY, "channel_ids": [str(CHANNEL.id), "999"]}
    )

    assert created.status_code == 200
    body = created.json()
    assert body["batch_id"] is not None
    (job,) = body["jobs"]
    assert job["channel_id"] == str(CHANNEL.id)
    assert (job["status"], job["progress"], job["origin"]) == ("pending", 0.0, "manual")
    assert job["range_start"].startswith("2025-12-31T16:00:00")
    assert body["skipped"] == [{"channel_id": "999", "reason": "not_found"}]

    listed = (await logged_in.get("/api/history/jobs")).json()
    assert [j["id"] for j in listed["jobs"]] == [job["id"]]
    (batch,) = listed["batches"]
    assert batch["batch_id"] == body["batch_id"]
    assert batch["status_counts"]["pending"] == 1 and batch["channel_count"] == 1
    assert listed["next_before_id"] is None


async def test_full_history_and_now(logged_in: httpx.AsyncClient, readable: None) -> None:
    response = await logged_in.post(
        "/api/history/jobs", json={"channel_ids": [str(CHANNEL.id)], "range_start": None}
    )
    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 1


async def test_nothing_to_fetch_returns_reasons(
    logged_in: httpx.AsyncClient, readable: None
) -> None:
    assert (await logged_in.post("/api/history/jobs", json=BODY)).status_code == 200

    again = (await logged_in.post("/api/history/jobs", json=BODY)).json()

    assert again["batch_id"] is None and again["jobs"] == []
    assert again["skipped"] == [{"channel_id": str(CHANNEL.id), "reason": "nothing_to_fetch"}]


@pytest.mark.parametrize(
    "changes",
    [
        {"range_start": "2026-01-02T00:00:00+00:00", "range_end": "2026-01-01T00:00:00+00:00"},
        {"range_start": "2026-01-01T00:00:00"},
        {"range_start": "2999-01-01T00:00:00+00:00", "range_end": None},
    ],
)
async def test_invalid_range(
    logged_in: httpx.AsyncClient, readable: None, changes: dict[str, object]
) -> None:
    response = await logged_in.post("/api/history/jobs", json={**BODY, **changes})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_range"


async def test_too_many_channels(logged_in: httpx.AsyncClient, readable: None) -> None:
    ids = [str(1000 + i) for i in range(51)]
    response = await logged_in.post("/api/history/jobs", json={**BODY, "channel_ids": ids})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "too_many_channels"


async def test_oversize_id_is_not_found(logged_in: httpx.AsyncClient, readable: None) -> None:
    response = await logged_in.post(
        "/api/history/jobs", json={**BODY, "channel_ids": ["9999999999999999999"]}
    )
    assert response.json()["skipped"] == [
        {"channel_id": "9999999999999999999", "reason": "not_found"}
    ]


async def test_requires_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/history/jobs")).status_code == 401
    assert (await client.post("/api/history/jobs", json=BODY)).status_code == 401


async def test_write_requires_origin(
    logged_in: httpx.AsyncClient, readable: None, factory: Factory
) -> None:
    response = await logged_in.post(
        "/api/history/jobs", json=BODY, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    async with factory() as session:
        assert await session.scalar(select(HistoryJob.id)) is None


# ---- 里程碑 2：总开关、设置、状态 ----------------------------------------------------

UUID_ZERO = "00000000-0000-0000-0000-000000000000"
SETTINGS = {
    "page_delay_min_s": 3,
    "page_delay_max_s": 6,
    "daily_page_limit": 1000,
    "run_minutes": 15,
    "rest_minutes": 4,
    "auto_gap_max_hours": 2,
}


async def test_control_pauses_and_resumes(logged_in: httpx.AsyncClient) -> None:  # covers: AC-8
    paused = (await logged_in.post("/api/history/control", json={"paused": True})).json()
    assert paused["paused_reason"] == "manual" and paused["paused_at"] is not None

    resumed = (await logged_in.post("/api/history/control", json={"paused": False})).json()
    assert resumed["paused_reason"] is None and resumed["paused_at"] is None


async def test_manual_pause_keeps_automatic_reason(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-8
    async with factory() as session, session.begin():
        await set_history_paused(session, "rate_limited")

    body = (await logged_in.post("/api/history/control", json={"paused": True})).json()

    assert body["paused_reason"] == "rate_limited"


async def test_settings_round_trip(logged_in: httpx.AsyncClient) -> None:  # covers: AC-19
    first = (await logged_in.get("/api/history/settings")).json()
    assert first["page_delay_min_s"] == 2 and first["daily_page_limit"] == 1500
    assert first["pages_used_24h"] == 0

    saved = await logged_in.put("/api/history/settings", json=SETTINGS)
    assert saved.status_code == 200
    assert (await logged_in.get("/api/history/settings")).json() == {
        **SETTINGS,
        "pages_used_24h": 0,
    }


@pytest.mark.parametrize(
    "change",
    [
        {"page_delay_min_s": 0},
        {"page_delay_max_s": 121},
        {"daily_page_limit": 49},
        {"daily_page_limit": 20001},
        {"run_minutes": 4},
        {"rest_minutes": 0},
        {"auto_gap_max_hours": 73},
        {"page_delay_min_s": 7},  # 最短间隔大于最长间隔
    ],
)
async def test_settings_out_of_range_is_not_saved(
    logged_in: httpx.AsyncClient, change: dict[str, int]
) -> None:  # covers: AC-19
    response = await logged_in.put("/api/history/settings", json={**SETTINGS, **change})

    assert response.status_code == 422
    assert (await logged_in.get("/api/history/settings")).json()["page_delay_min_s"] == 2


async def test_settings_report_pages_used(logged_in: httpx.AsyncClient, factory: Factory) -> None:
    async with factory() as session, session.begin():
        await record_pages(session, 12)

    assert (await logged_in.get("/api/history/settings")).json()["pages_used_24h"] == 12


async def test_status_reports_quota_and_waiting(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-6, AC-20
    async with factory() as session, session.begin():
        await record_collector_heartbeat(session)
        await record_history_heartbeat(session)
        await record_pages(session, 1500)
        await set_history_waiting(session, "rest", datetime.now(UTC) + timedelta(minutes=3))

    body = (await logged_in.get("/api/system/status")).json()["history"]

    assert body["pages_used_24h"] == 1500 and body["quota_resumes_at"] is not None
    assert body["waiting_reason"] == "rest" and body["stale"] is False
    assert body["last_seen_at"] is not None


async def test_status_ignores_expired_waiting_and_flags_silent_worker(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-20
    async with factory() as session, session.begin():
        await record_collector_heartbeat(session)
        await set_history_waiting(session, "quota", datetime.now(UTC) - timedelta(minutes=1))

    body = (await logged_in.get("/api/system/status")).json()["history"]

    assert body["waiting_reason"] is None and body["waiting_until"] is None
    # 进程心跳正常而历史任务心跳为空：无响应。
    assert body["last_seen_at"] is None and body["stale"] is True


async def test_status_not_stale_when_process_is_down(
    logged_in: httpx.AsyncClient,
) -> None:  # covers: AC-20
    assert (await logged_in.get("/api/system/status")).json()["history"]["stale"] is False


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/history/control", {"paused": True}),
        ("GET", "/api/history/settings", None),
        ("PUT", "/api/history/settings", SETTINGS),
        ("POST", "/api/history/jobs/1/pause", None),
        ("POST", f"/api/history/batches/{UUID_ZERO}/pause", None),
    ],
)
async def test_new_endpoints_require_login(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, object] | None
) -> None:  # covers: AC-22
    response = await client.request(method, path, json=body)

    assert response.status_code == 401


# ---- 里程碑 3：任务操作 -------------------------------------------------------------


async def make_job(client: httpx.AsyncClient) -> dict[str, str]:
    created = await client.post("/api/history/jobs", json=BODY)
    return created.json()["jobs"][0]


async def test_job_actions_follow_the_state_table(
    logged_in: httpx.AsyncClient, readable: None
) -> None:  # covers: AC-9
    job = await make_job(logged_in)
    url = f"/api/history/jobs/{job['id']}"

    paused = await logged_in.post(f"{url}/pause")
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    again = await logged_in.post(f"{url}/pause")
    assert again.status_code == 409 and again.json()["error"]["code"] == "invalid_transition"
    assert (await logged_in.post(f"{url}/retry")).status_code == 409
    assert (await logged_in.post(f"{url}/resume")).json()["status"] == "pending"
    cancelled = (await logged_in.post(f"{url}/cancel")).json()
    assert cancelled["status"] == "cancelled" and cancelled["finished_at"] is not None


async def test_job_action_errors(logged_in: httpx.AsyncClient, readable: None) -> None:
    missing = await logged_in.post("/api/history/jobs/999999/pause")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "job_not_found"
    huge = await logged_in.post("/api/history/jobs/9999999999999999999/pause")
    assert huge.status_code == 404
    job = await make_job(logged_in)
    assert (await logged_in.post(f"/api/history/jobs/{job['id']}/explode")).status_code == 422


async def test_batch_actions_report_changed_rows(
    logged_in: httpx.AsyncClient, readable: None
) -> None:  # covers: AC-9
    created = (await logged_in.post("/api/history/jobs", json=BODY)).json()
    url = f"/api/history/batches/{created['batch_id']}"

    assert (await logged_in.post(f"{url}/pause")).json() == {"changed": 1}
    assert (await logged_in.post(f"{url}/pause")).json() == {"changed": 0}
    assert (await logged_in.post(f"{url}/resume")).json() == {"changed": 1}
    assert (await logged_in.post(f"{url}/cancel")).json() == {"changed": 1}
    missing = await logged_in.post(f"/api/history/batches/{UUID_ZERO}/cancel")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "batch_not_found"
    assert (await logged_in.post("/api/history/batches/not-a-uuid/cancel")).status_code == 422
    assert (await logged_in.post(f"{url}/retry")).status_code == 422


async def test_job_actions_require_origin(
    logged_in: httpx.AsyncClient, readable: None, factory: Factory
) -> None:  # covers: AC-22
    job = await make_job(logged_in)

    response = await logged_in.post(
        f"/api/history/jobs/{job['id']}/cancel", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    async with factory() as session:
        assert await session.scalar(select(HistoryJob.status)) == "pending"


async def test_finished_view_pages_and_includes_cancelled(
    logged_in: httpx.AsyncClient, readable: None
) -> None:  # covers: AC-14
    first = await make_job(logged_in)
    await logged_in.post(f"/api/history/jobs/{first['id']}/cancel")

    page = (await logged_in.get("/api/history/jobs", params={"view": "finished"})).json()
    active = (await logged_in.get("/api/history/jobs")).json()

    assert [j["id"] for j in page["jobs"]] == [first["id"]]
    assert active["jobs"] == []


async def test_channel_scoped_batch_action(logged_in: httpx.AsyncClient, readable: None) -> None:
    created = (await logged_in.post("/api/history/jobs", json=BODY)).json()
    base = f"/api/history/batches/{created['batch_id']}/channels"

    assert (await logged_in.post(f"{base}/{CHANNEL.id}/pause")).json() == {"changed": 1}
    assert (await logged_in.post(f"{base}/{CHANNEL.id}/pause")).json() == {"changed": 0}
    missing = await logged_in.post(f"{base}/999/cancel")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "batch_not_found"
