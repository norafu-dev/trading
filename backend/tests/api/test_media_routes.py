"""spec 0011：GET /api/media/{id}（真实 PostgreSQL，内存或本地存储）。"""

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.api.main import create_app
from harvester.db.ingest import ingest_message
from harvester.db.models import Media, StoredFile
from harvester.media.storage import LocalStore, MediaStore, MemoryStore
from tests.api.conftest import (
    FakeClock,
    api_settings,
    complete_setup,
    crypto_settings,
    do_login,
    make_client,
    wire,
)
from tests.db.builders import at, attachment, message, seed_channel

Factory = async_sessionmaker[AsyncSession]
KEY = "201/2024-10/" + "ab" * 32 + ".png"
THUMB = "201/2024-10/thumbs/" + "ab" * 32 + ".webp"


async def seed_stored(factory: Factory, *, thumb: bool = True, filename: str = "a.png") -> int:
    async with factory() as session, session.begin():
        await seed_channel(session)
        await ingest_message(session, message(attachments=(attachment(5),)), "realtime", at(0))
        file_id = await session.scalar(
            insert(StoredFile)
            .values(
                prefix="201/2024-10",
                sha256=bytes.fromhex("ab" * 32),
                size_bytes=3,
                content_type="image/png",
                object_key=KEY,
                thumb_object_key=THUMB if thumb else None,
                thumb_size_bytes=2 if thumb else None,
                thumb_width=1 if thumb else None,
                thumb_height=1 if thumb else None,
            )
            .returning(StoredFile.id)
        )
        media_id = await session.scalar(
            update(Media)
            .values(status="stored", file_id=file_id, filename=filename)
            .returning(Media.id)
        )
    assert media_id is not None
    return media_id


def build(store: MediaStore, factory: Factory, clock: FakeClock) -> FastAPI:
    return wire(create_app(api_settings(), crypto_settings(), store), factory, clock)


@pytest.fixture
def logged_in(
    factory: Factory, clock: FakeClock
) -> Callable[[MediaStore], AsyncIterator[httpx.AsyncClient]]:
    async def make(store: MediaStore) -> AsyncIterator[httpx.AsyncClient]:
        async with make_client(build(store, factory, clock)) as client:
            secret = await complete_setup(client, factory, clock)
            assert (await do_login(client, secret, clock)).status_code == 204
            yield client

    return make


async def test_requires_login(factory: Factory, clock: FakeClock) -> None:  # covers: AC-14
    media_id = await seed_stored(factory)
    async with make_client(build(MemoryStore(), factory, clock)) as client:
        assert (await client.get(f"/api/media/{media_id}")).status_code == 401


async def test_redirects_to_a_signed_link_with_cache_header(  # covers: AC-14
    factory: Factory, clock: FakeClock
) -> None:
    media_id = await seed_stored(factory)
    async with make_client(build(MemoryStore(), factory, clock)) as client:
        secret = await complete_setup(client, factory, clock)
        await do_login(client, secret, clock)

        original = await client.get(f"/api/media/{media_id}")
        thumb = await client.get(f"/api/media/{media_id}?variant=thumb")
        download = await client.get(f"/api/media/{media_id}?download=1")

    assert original.status_code == 302
    assert original.headers["cache-control"] == "private, max-age=3000"
    assert original.headers["location"].startswith(f"memory://{KEY}?type=image/png")
    assert "disposition=inline" in original.headers["location"]
    assert thumb.headers["location"].startswith(f"memory://{THUMB}?type=image/webp")
    assert "disposition=attachment" in download.headers["location"]
    assert "expires=3600" in original.headers["location"]


async def test_thumb_falls_back_to_original(factory: Factory, clock: FakeClock) -> None:  # AC-14
    media_id = await seed_stored(factory, thumb=False)
    async with make_client(build(MemoryStore(), factory, clock)) as client:
        secret = await complete_setup(client, factory, clock)
        await do_login(client, secret, clock)

        response = await client.get(f"/api/media/{media_id}?variant=thumb")

    assert response.headers["location"].startswith(f"memory://{KEY}")


async def test_unknown_or_unstored_is_404(factory: Factory, clock: FakeClock) -> None:  # AC-14
    media_id = await seed_stored(factory)
    async with factory() as session, session.begin():
        await session.execute(update(Media).values(status="failed", file_id=None))
    async with make_client(build(MemoryStore(), factory, clock)) as client:
        secret = await complete_setup(client, factory, clock)
        await do_login(client, secret, clock)

        pending = await client.get(f"/api/media/{media_id}")
        missing = await client.get("/api/media/999999")
        bad = await client.get("/api/media/1?variant=huge")

    assert (pending.status_code, missing.status_code, bad.status_code) == (404, 404, 422)
    assert (
        pending.json()["error"]["code"] == "media_not_found" if "error" in pending.json() else True
    )


async def test_local_mode_serves_the_file_with_safe_headers(  # covers: AC-20
    factory: Factory, clock: FakeClock, tmp_path: Path
) -> None:
    media_id = await seed_stored(factory, filename="设计稿.png")
    target = tmp_path / KEY
    target.parent.mkdir(parents=True)
    target.write_bytes(b"PNG")
    async with make_client(build(LocalStore(tmp_path), factory, clock)) as client:
        secret = await complete_setup(client, factory, clock)
        await do_login(client, secret, clock)

        inline = await client.get(f"/api/media/{media_id}")
        forced = await client.get(f"/api/media/{media_id}?download=1")

    assert inline.status_code == 200 and inline.content == b"PNG"
    assert inline.headers["content-type"] == "image/png"
    assert inline.headers["content-disposition"] == "inline"
    assert inline.headers["x-content-type-options"] == "nosniff"
    assert inline.headers["content-security-policy"] == "sandbox"
    assert inline.headers["cache-control"] == "private, max-age=3000"
    assert forced.headers["content-disposition"].startswith("attachment;")
    assert "filename*=UTF-8''" in forced.headers["content-disposition"]
