"""下载任务（spec 0011）：真实 PostgreSQL，假的 CDN（httpx.MockTransport），内存存储。"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.collector import media_worker
from harvester.collector.media_worker import MediaDeps, PauseState
from harvester.db import media as media_db
from harvester.db.ingest import ingest_message
from harvester.db.models import Channel, Media, StoredFile
from harvester.db.snapshots import AttachmentSnapshot
from harvester.media.storage import MemoryStore
from tests.db.builders import CHANNEL, at, message, seed_channel

Factory = async_sessionmaker[AsyncSession]
URL = "https://cdn.discordapp.com/attachments/201/5001/a.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


@pytest.fixture
def factory(engine: AsyncEngine) -> Factory:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def seeded(factory: Factory) -> Factory:
    async with factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=True)
        )
    return factory


def snapshot_attachment(attachment_id: int = 5001, url: str = URL, size: int = 108):
    return AttachmentSnapshot(
        id=attachment_id,
        filename="a.png",
        content_type="image/png",
        size_bytes=size,
        url=url,
        width=10,
        height=10,
    )


async def ingest(factory: Factory, message_id: int, *attachments: AttachmentSnapshot) -> None:
    async with factory() as session, session.begin():
        await ingest_message(
            session, message(id=message_id, attachments=attachments), "realtime", at(0)
        )


def make_deps(
    factory: Factory,
    handler: Callable[[httpx.Request], httpx.Response],
    tmp_path: Path,
    store: MemoryStore | None = None,
) -> MediaDeps:
    tmp_dir = tmp_path / "dl"
    tmp_dir.mkdir(exist_ok=True)
    return MediaDeps(
        factory=factory,
        store=store or MemoryStore(),
        http=httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
        ),
        tmp_dir=tmp_dir,
        wake=asyncio.Event(),
        pause=PauseState(),
        clock=lambda: datetime.now(UTC),
    )


async def run_once(deps: MediaDeps) -> int:
    """领取一批并处理完，返回领取的行数。"""
    async with deps.factory() as session, session.begin():
        claimed = await media_db.claim_batch(session, 10)
    for row in claimed:
        await media_worker.process_row(deps, row)
    return len(claimed)


async def all_media(factory: Factory) -> list[Media]:
    async with factory() as session:
        return list(await session.scalars(select(Media).order_by(Media.id)))


async def test_ingest_creates_pending_media_row(seeded: Factory) -> None:  # covers: AC-1
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())

    rows = await all_media(seeded)
    assert [(r.kind, r.status, r.attachment_id) for r in rows] == [("attachment", "pending", 5001)]


async def test_download_stores_file_and_notifies(  # covers: AC-1, AC-4, AC-16
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    store = MemoryStore()
    deps = make_deps(seeded, lambda _r: httpx.Response(200, content=PNG), tmp_path, store)

    assert await run_once(deps) == 1

    [row] = await all_media(seeded)
    assert row.status == "stored" and row.file_id is not None
    async with seeded() as session:
        stored = (await session.scalars(select(StoredFile))).one()
    assert stored.object_key == f"201/2024-10/{stored.sha256.hex()}.png"
    assert store.objects[stored.object_key] == PNG
    assert stored.size_bytes == len(PNG)


async def test_same_content_same_month_is_stored_once(  # covers: AC-4
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment(5001))
    await ingest(
        seeded,
        1_300_000_000_000_000_002,
        snapshot_attachment(5002, "https://cdn.discordapp.com/attachments/201/5002/b.png"),
    )
    store = MemoryStore()
    deps = make_deps(seeded, lambda _r: httpx.Response(200, content=PNG), tmp_path, store)

    await run_once(deps)

    rows = await all_media(seeded)
    assert {r.file_id for r in rows} == {rows[0].file_id} and rows[0].file_id is not None
    assert len(store.objects) == 1


async def test_declared_size_over_limit_is_too_large_without_request(  # covers: AC-6
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment(size=60 * 1024 * 1024))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=PNG)

    await run_once(make_deps(seeded, handler, tmp_path))

    [row] = await all_media(seeded)
    assert row.status == "too_large" and requests == []


async def test_unknown_size_is_aborted_when_it_passes_the_limit(  # covers: AC-6
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    async with seeded() as session, session.begin():
        await session.execute(text("UPDATE media_settings SET max_file_bytes = 1048576"))
    big = b"x" * (1048576 + 10)

    await run_once(make_deps(seeded, lambda _r: httpx.Response(200, content=big), tmp_path))

    [row] = await all_media(seeded)
    assert row.status == "too_large" and row.aborted_at_bytes == 1048576


@pytest.mark.parametrize(
    ("status", "expected_attempts", "expected_status", "needs_refresh"),
    [(500, 1, "pending", False), (429, 1, "pending", False), (404, 0, "pending", True)],
)
async def test_failure_classification(  # covers: AC-10, AC-11
    seeded: Factory,
    tmp_path: Path,
    status: int,
    expected_attempts: int,
    expected_status: str,
    needs_refresh: bool,
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())

    await run_once(make_deps(seeded, lambda _r: httpx.Response(status), tmp_path))

    [row] = await all_media(seeded)
    assert (row.attempts, row.status, row.needs_refresh) == (
        expected_attempts,
        expected_status,
        needs_refresh,
    )
    assert row.claimed_at is None


async def test_fifth_retryable_failure_becomes_failed(  # covers: AC-10
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    deps = make_deps(seeded, lambda _r: httpx.Response(503), tmp_path)

    for _ in range(5):
        async with seeded() as session, session.begin():
            await session.execute(update(Media).values(next_attempt_at=text("now()")))
        await run_once(deps)

    [row] = await all_media(seeded)
    assert (row.status, row.attempts, row.last_error) == ("failed", 5, "http_503")


async def test_redirect_is_gone_and_never_followed(  # covers: AC-3
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    await run_once(make_deps(seeded, handler, tmp_path))

    [row] = await all_media(seeded)
    assert row.status == "gone" and seen == ["cdn.discordapp.com"]


async def test_stale_claim_result_is_discarded(  # covers: AC-18
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG)

    deps = make_deps(seeded, handler, tmp_path)
    async with deps.factory() as session, session.begin():
        [claimed] = await media_db.claim_batch(session, 1)
    # 下载期间这一行被重置或手动重试：claimed_at 被清空。
    async with seeded() as session, session.begin():
        await session.execute(update(Media).values(claimed_at=None))

    await media_worker.process_row(deps, claimed)

    [row] = await all_media(seeded)
    assert row.status == "pending" and row.file_id is None


async def test_startup_releases_claims_without_counting_attempts(  # covers: AC-18
    seeded: Factory, tmp_path: Path
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    async with seeded() as session, session.begin():
        await media_db.claim_batch(session, 1)
    leftover = tmp_path / "dl" / "half"
    leftover.parent.mkdir()
    leftover.write_bytes(b"partial")

    await media_worker.prepare_start(seeded, tmp_path / "dl")

    [row] = await all_media(seeded)
    assert row.claimed_at is None and row.attempts == 0
    assert not leftover.exists()


async def test_closed_channel_rows_are_not_claimed(  # covers: AC-23
    seeded: Factory,
) -> None:
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment())
    async with seeded() as session, session.begin():
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=False)
        )
        assert await media_db.claim_batch(session, 5) == ()
        await session.execute(
            update(Channel).where(Channel.id == CHANNEL.id).values(collect_enabled=True)
        )
        assert len(await media_db.claim_batch(session, 5)) == 1


async def test_newest_messages_are_claimed_first(seeded: Factory) -> None:  # covers: AC-12
    await ingest(seeded, 1_300_000_000_000_000_001, snapshot_attachment(5001))
    await ingest(
        seeded,
        1_300_000_000_000_000_009,
        snapshot_attachment(5002, "https://cdn.discordapp.com/attachments/201/5002/b.png"),
    )

    async with seeded() as session, session.begin():
        claimed = await media_db.claim_batch(session, 1)

    assert [c.message_id for c in claimed] == [1_300_000_000_000_000_009]
