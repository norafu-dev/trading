"""spec 0014：导出预览和下载接口（真实 PostgreSQL）。"""

import csv
import io
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import insert, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.api import export_routes
from harvester.db import export as export_store
from harvester.db.history import ChannelGaps
from harvester.db.ingest import ingest_message, mark_messages_deleted, upsert_channel
from harvester.db.models import Media, StoredFile
from harvester.db.snapshots import AttachmentSnapshot, ChannelSnapshot
from tests.api.conftest import ORIGIN
from tests.db.builders import (
    CHANNEL,
    MESSAGE_ID,
    OTHER_CHANNEL,
    at,
    attachment,
    message,
    seed_channel,
    seed_other_channel,
)

Factory = async_sessionmaker[AsyncSession]
EXPORT = f"/api/channels/{CHANNEL.id}/export"
PREVIEW = f"{EXPORT}/preview"
THREAD = ChannelSnapshot(id=202, guild_id=100, parent_id=CHANNEL.id, type=11, name="t", position=0)


def mid(index: int) -> int:
    return MESSAGE_ID + (index << 22)


async def put(factory: Factory, index: int, content: str = "m", **changes: Any) -> int:
    async with factory() as session, session.begin():
        await seed_channel(session)
        snap = message(id=mid(index), content=content, **changes)
        assert await ingest_message(session, snap, "realtime", at(index)) == "inserted"
    return mid(index)


async def put_many(factory: Factory, count: int) -> list[int]:
    return [await put(factory, i, f"m{i}") for i in range(1, count + 1)]


def csv_rows(body: str) -> list[dict[str, str]]:
    assert body.startswith("﻿")
    return list(csv.DictReader(io.StringIO(body[1:], newline="")))


def jsonl(body: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in body.splitlines()]


async def download(client: httpx.AsyncClient, **params: str) -> httpx.Response:
    params.setdefault("format", "csv")
    params.setdefault("tz", "UTC")
    return await client.get(EXPORT, params=params)


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


# --- 登录、参数、范围（AC-1、AC-18、AC-21） -----------------------------------


@pytest.mark.parametrize("path", [EXPORT + "?format=csv&tz=UTC", PREVIEW])
async def test_requires_login(client: httpx.AsyncClient, path: str) -> None:  # covers: AC-21
    assert (await client.get(path)).status_code == 401


async def test_channel_and_message_not_found(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-1, AC-18
    first = await put(factory, 1)
    async with factory() as session, session.begin():
        await seed_other_channel(session)
        assert await upsert_channel(session, THREAD) == "ok"
        other = message(id=mid(9), channel_id=OTHER_CHANNEL.id, guild_id=OTHER_CHANNEL.guild_id)
        in_thread = message(id=mid(10), channel_id=THREAD.id)
        await ingest_message(session, other, "realtime", at(9))
        await ingest_message(session, in_thread, "realtime", at(10))

    missing = await logged_in.get("/api/channels/999/export/preview")
    assert missing.status_code == 404 and error_code(missing) == "channel_not_found"
    for bad in (mid(9), mid(10), mid(99)):
        for response in (
            await logged_in.get(PREVIEW, params={"start": str(first), "end": str(bad)}),
            await download(logged_in, start=str(bad)),
        ):
            assert response.status_code == 404
            assert error_code(response) == "message_not_found"

    # 在线程页导出的是线程自己的消息，普通频道不含线程里的。
    body = (await download(logged_in)).text
    assert [r["id"] for r in csv_rows(body)] == [str(first)]
    thread = await logged_in.get(
        f"/api/channels/{THREAD.id}/export", params={"format": "csv", "tz": "UTC"}
    )
    assert [r["id"] for r in csv_rows(thread.text)] == [str(mid(10))]


async def test_invalid_params(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-18
    await put(factory, 1)
    response = await logged_in.get(EXPORT, params={"start": "abc", "format": "xlsx", "tz": "../x"})
    assert response.status_code == 422
    assert error_code(response) == "invalid_export"
    assert {tuple(f["loc"]) for f in response.json()["error"]["fields"]} == {
        ("query", "start"),
        ("query", "format"),
        ("query", "tz"),
    }
    # 预览不检查 format 和 tz。
    assert (await logged_in.get(PREVIEW, params={"tz": "nope"})).status_code == 200
    bad = await logged_in.get(PREVIEW, params={"end": "-1"})
    assert bad.status_code == 422 and error_code(bad) == "invalid_export"


# --- 下载内容（AC-10 到 AC-16） ----------------------------------------------


async def test_csv_range_inclusive_ordered_with_edits_and_deletes(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-10, AC-11, AC-12, AC-13, AC-14, AC-15
    ids = await put_many(factory, 5)
    async with factory() as session, session.begin():
        await ingest_message(
            session,
            message(id=ids[2], content="改过 <@1> =1", edited_at=at(20)),
            "realtime",
            at(20),
        )
        await mark_messages_deleted(session, [ids[3]], at(30), "realtime")

    # 两端按 ID 排序，和传入的先后无关。
    response = await download(logged_in, start=str(ids[3]), end=str(ids[1]), tz="Asia/Shanghai")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="channel-201_')
    assert unquote(disposition.split("filename*=UTF-8''")[1]).startswith("Signals_alerts_")
    assert "\r\n" in response.text

    rows = csv_rows(response.text)
    assert [r["id"] for r in rows] == [str(i) for i in ids[1:4]]
    edited = rows[1]
    assert edited["content"] == "改过  =1"
    assert edited["version"] == "2"
    assert edited["edited_at"].endswith("+08:00")
    assert rows[2]["deleted_at"].endswith("+08:00")
    assert rows[0]["created_at"].endswith("+08:00")


async def test_jsonl_includes_revisions(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-16
    [first] = await put_many(factory, 1)
    async with factory() as session, session.begin():
        await ingest_message(
            session, message(id=first, content="v2", edited_at=at(5)), "realtime", at(5)
        )
    response = await download(logged_in, start=str(first), end=str(first), format="jsonl")
    assert response.headers["content-type"] == "application/x-ndjson; charset=utf-8"
    assert not response.text.startswith("﻿")
    [record] = jsonl(response.text)
    assert record["id"] == str(first)
    assert record["content"] == "v2"
    assert record["version"] == 2
    assert [(r["version"], r["content"]) for r in record["revisions"]] == [(1, "m1")]


async def test_attachments_links_and_removed(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-17
    async with factory() as session, session.begin():
        await seed_channel(session)
        fwd_att = AttachmentSnapshot(
            id=90,
            filename="f.jpg",
            content_type="image/jpeg",
            size_bytes=7,
            url="u",
            width=None,
            height=None,
        )
        snap = message(
            id=mid(1),
            attachments=(attachment(5), attachment(6), attachment(7)),
            forwarded={
                "message_id": 1,
                "channel_id": 2,
                "guild_id": None,
                "content": "fwd",
                "embeds": [],
                "attachment_names": ["f.jpg"],
            },
            forwarded_attachments=(fwd_att,),
        )
        await ingest_message(session, snap, "realtime", at(1))
        # 附件 7 被移除：编辑时附件列表里没有它。
        await ingest_message(
            session,
            message(
                id=mid(1),
                attachments=(attachment(5), attachment(6)),
                edited_at=at(2),
                forwarded=snap.forwarded,
            ),
            "realtime",
            at(2),
        )
        file_id = await session.scalar(
            insert(StoredFile)
            .values(
                prefix="201/2024-10",
                sha256=bytes.fromhex("ab" * 32),
                size_bytes=3,
                content_type="image/png",
                object_key="k",
            )
            .returning(StoredFile.id)
        )
        stored_id = await session.scalar(
            update(Media)
            .where(Media.attachment_id == 5)
            .values(status="stored", file_id=file_id)
            .returning(Media.id)
        )
        await session.execute(
            update(Media).where(Media.attachment_id == 6).values(status="too_large")
        )
        fwd_media = await session.scalar(
            update(Media)
            .where(Media.kind == "forwarded_attachment")
            .values(status="stored", file_id=file_id)
            .returning(Media.id)
        )

    csv_row = csv_rows((await download(logged_in)).text)[0]
    assert csv_row["attachments"] == f"file-5.png {ORIGIN}/api/media/{stored_id}\nfile-6.png"
    assert csv_row["forwarded"] == f"fwd\nf.jpg {ORIGIN}/api/media/{fwd_media}"

    [record] = jsonl((await download(logged_in, format="jsonl")).text)
    by_id = {a["id"]: a for a in record["attachments"]}
    assert by_id["5"]["url"] == f"{ORIGIN}/api/media/{stored_id}"
    assert by_id["6"]["url"] is None and by_id["6"]["media_status"] == "too_large"
    assert by_id["7"]["removed_at"] is not None
    assert record["forwarded"]["attachments"][0]["content_type"] == "image/jpeg"
    assert record["forwarded"]["attachments"][0]["url"] == f"{ORIGIN}/api/media/{fwd_media}"


async def test_embed_image_copy_links(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-17
    async with factory() as session, session.begin():
        await seed_channel(session)
        embeds = (
            {
                "description": "first",
                "image_url": "https://cdn.discordapp.com/attachments/1/2/a.png",
            },
            {
                "description": "second",
                "image_url": "https://cdn.discordapp.com/attachments/1/3/b.png",
                "thumbnail_url": "https://cdn.discordapp.com/attachments/1/4/t.png",
            },
        )
        await ingest_message(session, message(id=mid(1), embeds=embeds), "realtime", at(1))
        file_id = await session.scalar(
            insert(StoredFile)
            .values(
                prefix="201/2024-10",
                sha256=bytes.fromhex("cd" * 32),
                size_bytes=3,
                content_type="image/png",
                object_key="k2",
            )
            .returning(StoredFile.id)
        )
        # 只有第二个 embed 的图片和缩略图保存好了，第一个 embed 的图还是 pending。
        stored = {
            (kind, slot): media_id
            for media_id, kind, slot in (
                await session.execute(
                    update(Media)
                    .where(Media.slot == 1, Media.kind.in_(("embed_image", "embed_thumbnail")))
                    .values(status="stored", file_id=file_id)
                    .returning(Media.id, Media.kind, Media.slot)
                )
            ).all()
        }
    assert set(stored) == {("embed_image", 1), ("embed_thumbnail", 1)}

    [record] = jsonl((await download(logged_in, format="jsonl")).text)
    first, second = record["embeds"]
    assert "image_media_url" not in first
    assert second["image_media_url"] == f"{ORIGIN}/api/media/{stored[('embed_image', 1)]}"
    assert second["thumbnail_media_url"] == f"{ORIGIN}/api/media/{stored[('embed_thumbnail', 1)]}"
    assert second["image_url"].endswith("b.png")

    csv_row = csv_rows((await download(logged_in)).text)[0]
    assert csv_row["embeds"] == (
        f"first\n\nsecond\n图片: {ORIGIN}/api/media/{stored[('embed_image', 1)]}"
        f"\n缩略图: {ORIGIN}/api/media/{stored[('embed_thumbnail', 1)]}"
    )


# --- 开放的一端、空频道、上限（AC-3、AC-6、AC-7、AC-9、AC-18） -----------------------


async def test_open_ends(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-3, AC-12
    ids = await put_many(factory, 4)
    to_latest = csv_rows((await download(logged_in, start=str(ids[2]))).text)
    assert [r["id"] for r in to_latest] == [str(i) for i in ids[2:]]
    from_earliest = csv_rows((await download(logged_in, end=str(ids[1]))).text)
    assert [r["id"] for r in from_earliest] == [str(i) for i in ids[:2]]
    whole = csv_rows((await download(logged_in)).text)
    assert len(whole) == 4

    preview = (await logged_in.get(PREVIEW, params={"start": str(ids[1])})).json()
    assert preview["count"] == 3 and preview["over_limit"] is False
    assert preview["range_start"]["id"] == str(ids[1])
    assert preview["range_end"]["id"] == str(ids[3])


async def test_empty_channel(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-9, 18
    async with factory() as session, session.begin():
        await seed_channel(session)
    preview = await logged_in.get(PREVIEW)
    assert preview.status_code == 200
    assert preview.json() == {
        "count": 0,
        "over_limit": False,
        "range_start": None,
        "range_end": None,
        "gaps": [],
    }
    response = await download(logged_in)
    assert response.status_code == 422 and error_code(response) == "export_empty"


async def test_over_limit(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-7
    await put_many(factory, 3)
    monkeypatch.setattr(export_store, "EXPORT_MAX_MESSAGES", 2)
    preview = (await logged_in.get(PREVIEW)).json()
    assert preview["count"] == 3 and preview["over_limit"] is True
    response = await download(logged_in)
    assert response.status_code == 422 and error_code(response) == "export_too_large"
    monkeypatch.setattr(export_store, "EXPORT_MAX_MESSAGES", 3)
    assert (await download(logged_in)).status_code == 200


async def test_preview_gaps_clipped(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-8
    ids = await put_many(factory, 3)
    base = await logged_in.get(PREVIEW, params={"start": str(ids[0]), "end": str(ids[2])})
    lo = datetime.fromisoformat(base.json()["range_start"]["created_at"])
    hi = datetime.fromisoformat(base.json()["range_end"]["created_at"])
    hour = timedelta(hours=1)

    async def fake_gaps(_session: AsyncSession, _ids: Any) -> tuple[ChannelGaps, ...]:
        gaps = (
            (lo - hour, lo + hour),  # 部分重叠
            (hi + hour, hi + 2 * hour),  # 范围之后，不重叠
            (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 1, 2, tzinfo=UTC)),
        )
        return (ChannelGaps(CHANNEL.id, "Signals", "alerts", gaps),)

    monkeypatch.setattr(export_routes, "channel_gaps", fake_gaps)
    closed = (
        await logged_in.get(PREVIEW, params={"start": str(ids[0]), "end": str(ids[2])})
    ).json()
    assert len(closed["gaps"]) == 1
    assert datetime.fromisoformat(closed["gaps"][0]["start"]) == lo
    assert datetime.fromisoformat(closed["gaps"][0]["end"]) == hi  # 裁到范围终点
    # 开放的终点算到现在（测试时钟是 2026-11-01）：范围后面那段 2024 年的缺口也算进来。
    open_end = (await logged_in.get(PREVIEW, params={"start": str(ids[0])})).json()
    assert len(open_end["gaps"]) == 2


async def test_count_timeout(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-20
    await put_many(factory, 1)

    class Canceled(Exception):
        sqlstate = "57014"

    async def slow(*_args: Any) -> int:
        raise DBAPIError("SELECT", {}, Canceled())

    monkeypatch.setattr(export_store, "count_capped", slow)
    for response in (await logged_in.get(PREVIEW), await download(logged_in)):
        assert response.status_code == 503 and error_code(response) == "export_timeout"


async def test_snapshot_transaction_settings(factory: Factory) -> None:  # covers: AC-12, AC-20
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        session = await export_routes._open_snapshot(stack, factory)  # pyright: ignore[reportPrivateUsage]
        values = [
            await session.scalar(text(f"SHOW {name}"))
            for name in (
                "transaction_isolation",
                "transaction_read_only",
                "statement_timeout",
                "idle_in_transaction_session_timeout",
            )
        ]
    assert values == ["repeatable read", "on", "30s", "5min"]


# --- 直接驱动 ASGI：快照、断开、中途失败（AC-12、AC-19、AC-21） ----------------------


Sender = Callable[[dict[str, Any]], Awaitable[None]]


async def call_asgi(
    app: FastAPI, client: httpx.AsyncClient, query: str, send: Sender, spec: str = "2.4"
) -> None:
    cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": EXPORT,
        "raw_path": EXPORT.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"cookie", cookie.encode()),
            (b"origin", ORIGIN.encode()),
            (b"user-agent", b"pytest-agent"),
        ],
        "client": ("203.0.113.7", 1234),
        "server": ("test", 80),
    }
    await app(scope, receive, send)


async def idle_in_transaction(factory: Factory) -> int:
    async with factory() as session:
        value = await session.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND state LIKE 'idle in transaction%'"
            )
        )
    return int(value or 0)


async def test_snapshot_ignores_concurrent_changes(
    app: FastAPI, logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-12
    ids = await put_many(factory, 3)
    monkeypatch.setattr(export_store, "BATCH_SIZE", 1)
    chunks: list[bytes] = []
    changed = False

    async def send(event: dict[str, Any]) -> None:
        nonlocal changed
        if event["type"] != "http.response.body":
            assert event["status"] == 200
            return
        chunks.append(event["body"])
        if len(chunks) == 2 and not changed:
            changed = True
            async with factory() as session, session.begin():
                await ingest_message(session, message(id=mid(50)), "realtime", at(50))
                await ingest_message(
                    session,
                    message(id=ids[2], content="later", edited_at=at(51)),
                    "realtime",
                    at(51),
                )
                await mark_messages_deleted(session, [ids[1]], at(52), "realtime")

    await call_asgi(app, logged_in, "format=csv&tz=UTC", send)
    assert changed
    rows = csv_rows(b"".join(chunks).decode())
    assert [r["id"] for r in rows] == [str(i) for i in ids]
    assert rows[2]["content"] == "m3"
    assert rows[1]["deleted_at"] == ""


async def test_disconnect_before_first_byte_releases_connection(
    app: FastAPI, logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-19, AC-21
    await put_many(factory, 2)

    async def send(_event: dict[str, Any]) -> None:
        raise OSError("client went away")

    with structlog.testing.capture_logs() as logs, pytest.raises(Exception):  # noqa: B017
        await call_asgi(app, logged_in, "format=csv&tz=UTC", send)
    assert await idle_in_transaction(factory) == 0
    events = [e["event"] for e in logs if e["event"].startswith("export_")]
    assert events == ["export_aborted"]


async def test_disconnect_mid_stream_releases_connection(
    app: FastAPI, logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-19
    await put_many(factory, 3)
    monkeypatch.setattr(export_store, "BATCH_SIZE", 1)
    sent = 0

    async def send(event: dict[str, Any]) -> None:
        nonlocal sent
        if event["type"] == "http.response.body":
            sent += 1
            if sent == 2:
                raise OSError("client went away")

    with structlog.testing.capture_logs() as logs, pytest.raises(Exception):  # noqa: B017
        await call_asgi(app, logged_in, "format=jsonl&tz=UTC", send)
    assert await idle_in_transaction(factory) == 0
    [event] = [e for e in logs if e["event"].startswith("export_")]
    assert event["event"] == "export_aborted"
    assert event["messages"] == 2  # 第 2 批已经取出，发送时断开


async def test_failure_mid_stream_drops_connection(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-19, AC-21
    await put_many(factory, 3)
    monkeypatch.setattr(export_store, "BATCH_SIZE", 1)
    real = export_store.fetch_batch
    calls = 0

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom")
        return await real(*args, **kwargs)

    monkeypatch.setattr(export_store, "fetch_batch", flaky)
    with structlog.testing.capture_logs() as logs, pytest.raises(RuntimeError):
        await download(logged_in)
    assert await idle_in_transaction(factory) == 0
    [event] = [e for e in logs if e["event"].startswith("export_")]
    assert event["event"] == "export_failed"
    assert event["messages"] == 1
    assert "content" not in event


async def test_finished_log_has_counts_not_content(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-21
    await put(factory, 1, "secret words")
    with structlog.testing.capture_logs() as logs:
        response = await download(logged_in)
    [event] = [e for e in logs if e["event"].startswith("export_")]
    assert event["event"] == "export_finished"
    assert event["messages"] == 1
    assert event["bytes"] == len(response.content)
    assert event["format"] == "csv" and event["channel_id"] == str(CHANNEL.id)
    assert "secret" not in json.dumps(event, default=str)
