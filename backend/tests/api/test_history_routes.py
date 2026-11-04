"""spec 0012：建任务和任务列表接口（真实 PostgreSQL）。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.models import AccountChannelAccess, HistoryJob
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
