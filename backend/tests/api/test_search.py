"""spec 0013：搜索接口、写入 search_text、固定计划（真实 PostgreSQL）。"""

import json
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.search import SearchFilters, search_ids_statement
from tests.db.builders import (
    CHANNEL,
    MESSAGE_ID,
    at,
    attachment,
    message,
    seed_channel,
)

Factory = async_sessionmaker[AsyncSession]
SEARCH = "/api/search"


async def put(factory: Factory, index: int, content: str, **changes: Any) -> int:
    message_id = MESSAGE_ID + (index << 22)
    async with factory() as session, session.begin():
        await seed_channel(session)
        result = await ingest_message(
            session, message(id=message_id, content=content, **changes), "realtime", at(index)
        )
        assert result == "inserted"
    return message_id


async def search(client: httpx.AsyncClient, **params: str) -> Any:
    response = await client.get(f"{SEARCH}/messages", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body: Any) -> list[str]:
    return [item["message"]["id"] for item in body["items"]]


@pytest.mark.parametrize(
    "path", ["/messages?q=btc", "/count?q=btc", "/status", "/channels", "/authors?q=ko"]
)
async def test_requires_login(client: httpx.AsyncClient, path: str) -> None:  # covers: AC-25
    assert (await client.get(SEARCH + path)).status_code == 401


async def test_keyword_search_newest_first_and_paging(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-3, AC-5, AC-12
    first = await put(factory, 1, "新空投活动 BTCUSDT")
    second = await put(factory, 2, "空投 and btc")
    await put(factory, 3, "只有空投")
    await put(factory, 4, "BTC only")

    body = await search(logged_in, q="空投 btc")
    assert ids(body) == [str(second), str(first)]
    assert body["next_before"] is None
    hit = body["items"][0]
    assert hit["guild"]["name"] == "Signals"
    assert hit["channel"]["name"] == "alerts"
    assert hit["channel"]["parent"] is None

    page = await search(logged_in, q="btc", limit="2")
    assert len(page["items"]) == 2
    assert page["next_before"] is not None
    rest = await search(logged_in, q="btc", before=page["next_before"])
    assert len(rest["items"]) == 1


async def test_exclude_phrase_and_special_characters(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-3, AC-5
    keep = await put(factory, 1, "btc 突破 50% 上涨")
    await put(factory, 2, "btc 止损 位 触发")
    await put(factory, 3, "btc 5000 上涨")

    assert ids(await search(logged_in, q='btc -"止损 位"', channel=str(CHANNEL.id)))[-1] == str(
        keep
    )
    assert ids(await search(logged_in, q="50%")) == [str(keep)]
    assert ids(await search(logged_in, q='"止损 位"')) != []
    assert str(keep) not in ids(await search(logged_in, q='"止损 位"'))


async def test_search_covers_embeds_attachments_and_deleted(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-6, AC-7
    with_file = await put(factory, 1, "chart", attachments=(attachment(900),))
    emoji = await put(factory, 2, "<:pepe:555> <@777>")
    assert ids(await search(logged_in, q="file-900")) == [str(with_file)]
    assert ids(await search(logged_in, q="pepe")) == [str(emoji)]
    assert ids(await search(logged_in, q="777")) == []

    async with factory() as session, session.begin():
        await mark_messages_deleted(session, [with_file], at(10), "realtime")
    body = await search(logged_in, q="file-900")
    assert body["items"][0]["message"]["deleted_at"] is not None


async def test_edit_updates_search_text(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-20
    message_id = await put(factory, 1, "oldword here")
    async with factory() as session, session.begin():
        result = await ingest_message(
            session,
            message(id=message_id, content="newword here", edited_at=at(5)),
            "realtime",
            at(6),
        )
        assert result == "edited"
    assert ids(await search(logged_in, q="oldword")) == []
    assert ids(await search(logged_in, q="newword")) == [str(message_id)]


async def test_unindexed_rows_hidden_from_keyword_but_not_filters(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-22
    message_id = await put(factory, 1, "pending word")
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE message SET search_text = NULL"))
    assert ids(await search(logged_in, q="pending")) == []
    assert ids(await search(logged_in, q="-other", channel=str(CHANNEL.id))) == []
    assert ids(await search(logged_in, channel=str(CHANNEL.id))) == [str(message_id)]
    status = await logged_in.get(f"{SEARCH}/status")
    assert status.json() == {"index_pending": 1}


async def test_count_and_invalid_params(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-4, AC-14
    await put(factory, 1, "count me")
    await put(factory, 2, "count me too")
    counted = await logged_in.get(f"{SEARCH}/count", params={"q": "count"})
    assert counted.json() == {"count": 2, "capped": False}

    bad = await logged_in.get(f"{SEARCH}/messages", params={"q": "a"})
    assert bad.status_code == 422
    error = bad.json()["error"]
    assert error["code"] == "invalid_query"
    assert error["fields"] == [{"loc": ["query", "q"], "type": "term_too_short"}]
    none = await logged_in.get(f"{SEARCH}/messages")
    assert none.json()["error"]["fields"][0]["type"] == "no_conditions"


async def test_timeout_is_503(
    logged_in: httpx.AsyncClient, factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-24
    await put(factory, 1, "slow query")
    monkeypatch.setattr("harvester.db.search.SEARCH_TIMEOUT_MS", 1)
    monkeypatch.setattr("harvester.db.search.COUNT_TIMEOUT_MS", 1)
    async with factory() as session, session.begin():
        await session.execute(text("SELECT 1"))
    # pg_sleep 不在搜索语句里，所以用极小的超时加大量行不可靠；直接模拟取消错误。
    from sqlalchemy.exc import DBAPIError

    class Canceled(Exception):
        sqlstate = "57014"

    async def boom(*_a: object, **_k: object) -> None:
        raise DBAPIError("stmt", {}, Canceled())

    monkeypatch.setattr("harvester.db.search._apply_plan_settings", boom)
    response = await logged_in.get(f"{SEARCH}/messages", params={"q": "slow"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "search_timeout"
    counted = await logged_in.get(f"{SEARCH}/count", params={"q": "slow"})
    assert counted.json() == {"count": None, "capped": False}


@pytest.mark.parametrize("term", ["空投", "btc"])
async def test_plan_uses_bigram_index(factory: Factory, term: str) -> None:  # covers: AC-21
    for i in range(1, 6):
        await put(factory, i, f"{term} message {i}")
    statement = search_ids_statement(SearchFilters(include=(term,)), None, 50)
    compiled = statement.compile(dialect=postgresql.asyncpg.dialect())
    values = [compiled.params[name] for name in compiled.positiontup or ()]
    async with factory() as session, session.begin():
        for setting in (
            "plan_cache_mode = force_custom_plan",
            "enable_indexscan = off",
            "enable_seqscan = off",
        ):
            await session.execute(text(f"SET LOCAL {setting}"))
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        # 带绑定参数的同一条语句，和生产一样走预备语句。
        rows = await raw.driver_connection.fetchval(  # pyright: ignore[reportOptionalMemberAccess, reportAttributeAccessIssue]
            "EXPLAIN (FORMAT JSON) " + str(compiled), *values
        )
        plan = json.dumps(rows)
    assert "ix_message_search_bigrams" in plan
    assert '"Node Type": "Seq Scan"' not in plan
    assert '"Index Name": "message_pkey"' not in plan


async def test_picker_and_authors(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-8, AC-9
    await put(factory, 1, "hello")
    picker = (await logged_in.get(f"{SEARCH}/channels")).json()
    assert [g["name"] for g in picker["guilds"]] == ["Signals"]
    channel = picker["guilds"][0]["channels"][0]
    assert channel["name"] == "alerts"
    assert channel["category_name"] == "Trading"

    found = (await logged_in.get(f"{SEARCH}/authors", params={"q": "KO"})).json()
    assert [a["username"] for a in found["authors"]] == ["kol"]
    assert (await logged_in.get(f"{SEARCH}/authors", params={"q": "zzz"})).json() == {"authors": []}
    by_id = (await logged_in.get(f"{SEARCH}/authors", params={"ids": "300,999"})).json()
    assert [a["id"] for a in by_id["authors"]] == ["300"]
    both = await logged_in.get(f"{SEARCH}/authors", params={"q": "a", "ids": "1"})
    assert both.status_code == 422


async def test_channel_and_thread_filters(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-8, AC-11
    first = await put(factory, 1, "shared word")
    async with factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO channel (id, guild_id, parent_id, type, name) VALUES "
                "(202, 100, 201, 11, 'thread'), (203, 100, 200, 0, 'sibling')"
            )
        )
    thread_msg = await put(factory, 2, "shared word", channel_id=202)
    await put(factory, 3, "shared word", channel_id=203)

    body = await search(logged_in, q="shared", channel="201")
    assert ids(body) == [str(thread_msg), str(first)]
    assert body["items"][0]["channel"]["parent"] == {"id": "201", "name": "alerts"}
    both = await search(logged_in, q="shared", guild="999", channel="201")
    assert len(both["items"]) == 2  # 服务器和频道是「或」
    other_author = await search(logged_in, q="shared", author="301")
    assert other_author["items"] == []


async def test_around_page(logged_in: httpx.AsyncClient, factory: Factory) -> None:
    # covers: AC-17, AC-18
    all_ids = [await put(factory, i, f"msg {i}") for i in range(1, 61)]
    base = f"/api/discord/channels/{CHANNEL.id}/messages"
    middle = all_ids[30]
    body = (await logged_in.get(base, params={"around": str(middle)})).json()
    got = [m["id"] for m in body["messages"]]
    assert got == [str(i) for i in all_ids[5:56]]
    assert body["has_more"] is True
    assert body["has_newer"] is True

    near_end = (await logged_in.get(base, params={"around": str(all_ids[-1])})).json()
    assert near_end["has_newer"] is False
    latest = (await logged_in.get(base)).json()
    assert latest["has_newer"] is None

    missing = await logged_in.get(base, params={"around": "12345"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "message_not_found"
    clash = await logged_in.get(base, params={"around": str(middle), "before": str(middle)})
    assert clash.status_code == 422


async def test_backfill_fills_missing_rows(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # covers: AC-22
    from harvester.api.search_backfill import backfill_batch

    first = await put(factory, 1, "legacy word", attachments=(attachment(901),))
    await put(factory, 2, "another legacy")
    async with factory() as session, session.begin():
        await session.execute(text("UPDATE message SET search_text = NULL"))
    assert (await logged_in.get(f"{SEARCH}/status")).json() == {"index_pending": 2}

    async with factory() as session, session.begin():
        assert await backfill_batch(session) == 2
    async with factory() as session, session.begin():
        assert await backfill_batch(session) == 0
    assert (await logged_in.get(f"{SEARCH}/status")).json() == {"index_pending": 0}
    assert ids(await search(logged_in, q="file-901")) == [str(first)]
