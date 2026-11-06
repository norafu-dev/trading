"""spec 0011：设置、重试和系统状态里的 media 块（真实 PostgreSQL）。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.ingest import ingest_message
from harvester.db.models import Channel, Media, StoredFile
from harvester.db.system import record_collector_heartbeat
from tests.api.conftest import FakeClock, complete_setup, do_login, make_client
from tests.db.builders import CHANNEL, at, attachment, message, seed_channel

Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


async def seed_rows(factory: Factory, *statuses: str) -> list[int]:
    """每个状态一行 media；too_large 的大小是 60 MB。"""
    ids: list[int] = []
    async with factory() as session, session.begin():
        await seed_channel(session)
        for index, _ in enumerate(statuses):
            snapshot = message(
                id=1_300_000_000_000_000_000 + index,
                attachments=(
                    attachment(
                        5000 + index, f"https://cdn.discordapp.com/attachments/1/{5000 + index}/a"
                    ),
                ),
            )
            await ingest_message(session, snapshot, "realtime", at(0))
        rows = list(await session.scalars(select(Media.id).order_by(Media.id)))
        for media_id, status in zip(rows, statuses, strict=True):
            values: dict[str, object] = {"status": status}
            if status == "too_large":
                values["declared_size_bytes"] = 60 * 1024**2
            await session.execute(update(Media).where(Media.id == media_id).values(**values))
            ids.append(media_id)
    return ids


async def statuses(factory: Factory) -> list[str]:
    async with factory() as session:
        return list(await session.scalars(select(Media.status).order_by(Media.id)))


async def test_settings_default_and_validation(  # covers: AC-8
    logged_in: httpx.AsyncClient,
) -> None:
    body = (await logged_in.get("/api/media/settings")).json()
    assert (body["max_file_mb"], body["quota_gb"], body["used_bytes"]) == (50, 50, 0)
    assert body["counts"] == {
        "pending": 0,
        "stored": 0,
        "failed": 0,
        "too_large": 0,
        "no_space": 0,
        "gone": 0,
    }
    for bad in (
        {"max_file_mb": 0, "quota_gb": 50},
        {"max_file_mb": 1025, "quota_gb": 50},
        {"max_file_mb": 50, "quota_gb": 0},
        {"max_file_mb": 50, "quota_gb": 10001},
    ):
        assert (await logged_in.put("/api/media/settings", json=bad)).status_code == 422
    ok = await logged_in.put("/api/media/settings", json={"max_file_mb": 1024, "quota_gb": 10000})
    assert ok.status_code == 200
    assert (ok.json()["max_file_mb"], ok.json()["quota_gb"]) == (1024, 10000)


async def test_raising_the_file_limit_requeues_only_what_now_fits(  # covers: AC-9
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed_rows(factory, "too_large", "pending")
    # 第二行改成未知大小、在 40 MB 处中止的 too_large。
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE media SET status = 'too_large', declared_size_bytes = NULL,"
                " aborted_at_bytes = :b WHERE id = 2"
            ),
            {"b": 40 * 1024**2},
        )

    await logged_in.put("/api/media/settings", json={"max_file_mb": 45, "quota_gb": 50})
    assert await statuses(factory) == ["too_large", "pending"]  # 60 MB 仍然放不下

    await logged_in.put("/api/media/settings", json={"max_file_mb": 61, "quota_gb": 50})
    assert await statuses(factory) == ["pending", "pending"]


async def test_raising_quota_requeues_no_space_rows(  # covers: AC-9
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed_rows(factory, "no_space")
    async with factory() as session, session.begin():
        file_id = await session.scalar(
            insert(StoredFile)
            .values(
                prefix="1/2024-10",
                sha256=b"\x01" * 32,
                size_bytes=2 * 1024**3,
                content_type="x/y",
                object_key="k",
            )
            .returning(StoredFile.id)
        )
        assert file_id is not None
    # 已用 2 GB，总量设 1 GB：满了，no_space 的行不重新排队。
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE media_settings SET quota_bytes = 1073741824"))
    full = await logged_in.get("/api/media/settings")
    assert full.json()["storage_full"] is True
    assert await statuses(factory) == ["no_space"]

    await logged_in.put("/api/media/settings", json={"max_file_mb": 50, "quota_gb": 3})

    assert await statuses(factory) == ["pending"]
    assert (await logged_in.get("/api/media/settings")).json()["storage_full"] is False


async def test_retry_rules(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-17
    ids = await seed_rows(factory, "failed", "gone", "too_large", "no_space", "pending")
    async with factory() as session, session.begin():
        await session.execute(update(Media).values(attempts=5, last_error="http_500"))

    failed = await logged_in.post(f"/api/media/{ids[0]}/retry")
    gone = await logged_in.post(f"/api/media/{ids[1]}/retry")
    too_large = await logged_in.post(f"/api/media/{ids[2]}/retry")
    no_space = await logged_in.post(f"/api/media/{ids[3]}/retry")
    pending = await logged_in.post(f"/api/media/{ids[4]}/retry")
    missing = await logged_in.post("/api/media/9999/retry")

    assert failed.status_code == 200 and failed.json()["status"] == "pending"
    assert gone.json()["status"] == "pending"
    assert [r.status_code for r in (too_large, no_space, pending)] == [409, 409, 409]
    assert too_large.json()["error"]["code"] == "media_not_retryable"
    assert missing.status_code == 404
    async with factory() as session:
        attempts = list(await session.scalars(select(Media.attempts).order_by(Media.id)))
    assert attempts[:2] == [0, 0]


async def test_retry_all_failed_leaves_gone_alone(  # covers: AC-17
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed_rows(factory, "failed", "failed", "gone")

    response = await logged_in.post("/api/media/retry-failed")

    assert response.json() == {"requeued": 2}
    assert await statuses(factory) == ["pending", "pending", "gone"]


async def test_pending_count_splits_out_closed_channels(  # covers: AC-23
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed_rows(factory, "pending", "pending")
    async with factory() as session, session.begin():
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=False)
        )

    body = (await logged_in.get("/api/media/settings")).json()

    assert (body["counts"]["pending"], body["pending_channel_off"]) == (2, 2)


async def test_new_endpoints_require_login(client: httpx.AsyncClient) -> None:  # covers: AC-21
    for method, path in (
        ("GET", "/api/media/settings"),
        ("PUT", "/api/media/settings"),
        ("POST", "/api/media/retry-failed"),
        ("POST", "/api/media/1/retry"),
    ):
        assert (await client.request(method, path)).status_code == 401


async def test_system_status_media_block(  # covers: AC-7, AC-22
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed_rows(factory, "no_space")
    async with factory() as session, session.begin():
        await record_collector_heartbeat(session)

    never_beat = (await logged_in.get("/api/system/status")).json()["media"]
    assert never_beat["storage_full"] is True
    assert never_beat["stale"] is True  # 进程正常，下载任务从没报过心跳

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE collector_heartbeat SET media_worker_seen_at = now(),"
                " media_paused_until = now() + interval '20 minutes'"
            )
        )
    healthy = (await logged_in.get("/api/system/status")).json()["media"]
    assert healthy["stale"] is False and healthy["paused_until"] is not None

    async with factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE collector_heartbeat"
                " SET media_worker_seen_at = now() - interval '200 seconds',"
                " media_paused_until = now() - interval '1 minute'"
            )
        )
    stale = (await logged_in.get("/api/system/status")).json()["media"]
    assert stale["stale"] is True and stale["paused_until"] is None
