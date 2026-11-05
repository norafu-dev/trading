"""spec 0013 补充：不写 search_text 的路径、补建并发、时间边界、封顶总数、日志、鉴权。"""

from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.api.search_backfill import backfill_batch
from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.models import Message
from harvester.db.snapshots import snowflake_time
from tests.api.test_search import SEARCH, ids, put, search
from tests.db.builders import CHANNEL, at, message

Factory = async_sessionmaker[AsyncSession]

SENTINEL = "sentinel"


async def set_sentinel(factory: Factory, message_id: int) -> None:
    async with factory() as session, session.begin():
        await session.execute(
            text("UPDATE message SET search_text = :t WHERE id = :id"),
            {"t": SENTINEL, "id": message_id},
        )


async def read_search_text(factory: Factory, message_id: int) -> str | None:
    async with factory() as session:
        return await session.scalar(
            text("SELECT search_text FROM message WHERE id = :id"), {"id": message_id}
        )


async def test_skipped_paths_keep_search_text(factory: Factory) -> None:  # covers: AC-20
    # 已删除：再来一次 ingest 不写。
    deleted = await put(factory, 1, "will be deleted")
    async with factory() as session, session.begin():
        await mark_messages_deleted(session, [deleted], at(5), "realtime")
    await set_sentinel(factory, deleted)
    async with factory() as session, session.begin():
        await ingest_message(
            session, message(id=deleted, content="changed after delete"), "realtime", at(6)
        )
    assert await read_search_text(factory, deleted) == SENTINEL

    # 只更新 edited_at、内容没变：不写。
    bumped = await put(factory, 2, "same content")
    await set_sentinel(factory, bumped)
    async with factory() as session, session.begin():
        await ingest_message(
            session,
            message(id=bumped, content="same content", edited_at=at(8), attachments_known=False),
            "realtime",
            at(9),
        )
    assert await read_search_text(factory, bumped) == SENTINEL

    # 完全没变化：不写。
    same = await put(factory, 3, "untouched")
    await set_sentinel(factory, same)
    async with factory() as session, session.begin():
        await ingest_message(
            session,
            message(id=same, content="untouched", attachments_known=False),
            "realtime",
            at(10),
        )
    assert await read_search_text(factory, same) == SENTINEL


async def test_message_orm_does_not_load_search_text() -> None:  # covers: AC-20
    assert "search_text" not in str(select(Message))


async def test_backfill_skips_rows_locked_by_a_writer(factory: Factory) -> None:  # covers: AC-22
    locked = await put(factory, 1, "locked row")
    await put(factory, 2, "free row")
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE message SET search_text = NULL"))

    async with factory() as writer, writer.begin():
        # 模拟 ingest 正在写这一行：持有行锁。
        await writer.execute(
            text("SELECT id FROM message WHERE id = :id FOR UPDATE"), {"id": locked}
        )
        async with factory() as session, session.begin():
            assert await backfill_batch(session) == 1  # 只补没被锁的那一条
        await writer.execute(
            text("UPDATE message SET search_text = 'written by ingest' WHERE id = :id"),
            {"id": locked},
        )
    # 补建之后再来一轮，也不会覆盖 ingest 写好的值。
    async with factory() as session, session.begin():
        assert await backfill_batch(session) == 0
    assert await read_search_text(factory, locked) == "written by ingest"


async def test_time_range_start_inclusive_end_exclusive(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-10
    first = await put(factory, 1, "tick one")
    second = await put(factory, 2, "tick two")
    third = await put(factory, 3, "tick three")
    boundary = snowflake_time(second).isoformat()

    assert ids(await search(logged_in, **{"q": "tick", "from": boundary})) == [
        str(third),
        str(second),
    ]
    assert ids(await search(logged_in, **{"q": "tick", "to": boundary})) == [str(first)]
    empty = await logged_in.get(
        f"{SEARCH}/messages",
        params={"q": "tick", "from": boundary, "to": boundary},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["fields"][0]["type"] == "empty_range"


async def test_count_is_capped(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-14
    for i in range(1, 5):
        await put(factory, i, "capped word")
    monkeypatch.setattr("harvester.db.search.COUNT_CAP", 3)
    body = (await logged_in.get(f"{SEARCH}/count", params={"q": "capped"})).json()
    assert body == {"count": 3, "capped": True}


async def test_logs_never_contain_keywords(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-25
    await put(factory, 1, "supersecretword inside")
    events: list[tuple[str, dict[str, Any]]] = []

    class Recorder:
        def info(self, event: str, **kw: Any) -> None:
            events.append((event, kw))

        warning = info

    monkeypatch.setattr("harvester.api.search_routes.log", Recorder())
    await search(logged_in, q="supersecretword")
    assert events
    assert "supersecretword" not in repr(events)
    assert "inside" not in repr(events)
    assert events[0][1]["results"] == 1


async def test_around_requires_login(client: httpx.AsyncClient) -> None:  # covers: AC-25
    response = await client.get(
        f"/api/discord/channels/{CHANNEL.id}/messages", params={"around": "1"}
    )
    assert response.status_code == 401
