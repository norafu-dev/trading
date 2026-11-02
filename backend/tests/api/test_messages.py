"""spec 0008：消息和频道详情接口（真实 PostgreSQL）。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db import channel_sync
from harvester.db.ingest import ingest_message, open_realtime_coverage
from harvester.db.models import Channel, Guild
from harvester.db.snapshots import ForwardedData
from tests.api.conftest import FakeClock, complete_setup, do_login, make_client
from tests.db.builders import (
    CHANNEL,
    GUILD,
    MESSAGE_ID,
    USER,
    at,
    attachment,
    make_account,
    message,
)
from tests.db.test_channel_sync import STATE

Factory = async_sessionmaker[AsyncSession]
BASE = f"/api/discord/channels/{CHANNEL.id}"
TOO_BIG = str(2**63)
BIG_ID = 2**63 - 1


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


async def seed(factory: Factory) -> int:
    async with factory() as session, session.begin():
        account_id = await make_account(session)
        await channel_sync.apply_guild_state(session, account_id, STATE, at(0))
    return account_id


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", BASE),
        ("GET", f"{BASE}/messages"),
        ("POST", f"/api/discord/guilds/{GUILD.id}/load-threads"),
    ],
)
async def test_requires_login(client: httpx.AsyncClient, method: str, path: str) -> None:  # AC-24
    assert (await client.request(method, path)).status_code == 401


async def test_cross_site_load_threads_is_rejected(logged_in: httpx.AsyncClient) -> None:  # AC-24
    response = await logged_in.post(
        f"/api/discord/guilds/{GUILD.id}/load-threads", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403


async def test_message_fields_and_string_ids(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # AC-14
    await seed(factory)
    forwarded = ForwardedData(
        message_id=BIG_ID,
        channel_id=BIG_ID - 1,
        guild_id=None,
        content="f",
        embeds=[],
        attachment_names=["a.png"],
    )
    snap = message(
        id=BIG_ID, attachments=(attachment(1),), forwarded=forwarded, embeds=({"title": "t"},)
    )
    async with factory() as session, session.begin():
        await ingest_message(session, snap, "realtime", at(0))

    body = (await logged_in.get(f"{BASE}/messages")).json()

    row = body["messages"][0]
    assert row["id"] == str(BIG_ID)
    assert row["author_id"] == str(USER.id)
    assert row["author_name"] == "KOL"
    assert row["forwarded"]["message_id"] == str(BIG_ID)
    assert row["forwarded"]["guild_id"] is None
    assert row["embeds"] == [{"title": "t"}]
    assert row["attachments"][0]["id"] == "1"
    assert row["reply"] is None
    assert body["has_more"] is False


@pytest.mark.parametrize(
    "query",
    [
        "before=abc",
        "after=-1",
        "before=0",
        f"before={TOO_BIG}",
        f"ids={TOO_BIG}",
        "ids=1,,2",
        "ids=",
        "before=1&after=2",
        "after=1&ids=2",
        "limit=0",
        "limit=201",
        "ids=" + ",".join(str(i) for i in range(1, 102)),
    ],
)
async def test_bad_parameters_are_422(
    logged_in: httpx.AsyncClient, factory: Factory, query: str
) -> None:  # AC-13
    await seed(factory)

    assert (await logged_in.get(f"{BASE}/messages?{query}")).status_code == 422


async def test_duplicate_ids_are_merged_before_the_limit(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # AC-13
    await seed(factory)
    async with factory() as session, session.begin():
        await ingest_message(session, message(), "realtime", at(0))
    ids = ",".join([str(MESSAGE_ID)] * 150)

    body = (await logged_in.get(f"{BASE}/messages?ids={ids}")).json()

    assert [m["id"] for m in body["messages"]] == [str(MESSAGE_ID)]


async def test_unknown_channel_is_404(logged_in: httpx.AsyncClient) -> None:  # AC-13
    for path in ("/api/discord/channels/9", "/api/discord/channels/9/messages"):
        response = await logged_in.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "channel_not_found"


async def test_oversized_path_id_is_422(logged_in: httpx.AsyncClient) -> None:  # AC-13
    assert (await logged_in.get(f"/api/discord/channels/{TOO_BIG}")).status_code == 422


async def test_detail_reports_starting_then_nothing(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # AC-23
    account_id = await seed(factory)
    assert (await logged_in.get(BASE)).json()["banner_reason"] == "collect_off"

    async with factory() as session, session.begin():
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collect_enabled=True, collector_account_id=account_id)
        )
    assert (await logged_in.get(BASE)).json()["banner_reason"] == "account_offline"

    from harvester.db import account as accounts

    async with factory() as session, session.begin():
        await accounts.record_status(session, account_id, 1, "online", None)
    detail = (await logged_in.get(BASE)).json()
    assert detail["banner_reason"] == "starting"
    assert detail["guild_name"] == GUILD.name

    async with factory() as session, session.begin():
        await open_realtime_coverage(session, CHANNEL.id, at(1))
    assert (await logged_in.get(BASE)).json()["banner_reason"] is None


async def test_load_threads_is_idempotent(
    logged_in: httpx.AsyncClient, factory: Factory, clock: FakeClock
) -> None:  # AC-12
    await seed(factory)
    path = f"/api/discord/guilds/{GUILD.id}/load-threads"

    first = await logged_in.post(path)
    assert (first.status_code, first.json()) == (202, {"pending": True})
    requested = clock()
    clock.advance(seconds=30)
    assert (await logged_in.post(path)).status_code == 202

    async with factory() as session:
        stored = await session.scalar(select(Guild.thread_load_requested_at))
    assert stored == requested
    guilds = (await logged_in.get("/api/discord/guilds")).json()["guilds"]
    assert guilds[0]["thread_load_pending"] is True


async def test_load_threads_errors(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-12
    path = f"/api/discord/guilds/{GUILD.id}/load-threads"
    missing = await logged_in.post(path)
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "guild_not_found")

    await seed(factory)
    async with factory() as session, session.begin():
        await session.execute(update(Guild).values(status="left"))
    left = await logged_in.post(path)
    assert (left.status_code, left.json()["error"]["code"]) == (409, "guild_unavailable")


async def test_load_threads_without_account(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # AC-12
    from harvester.db.ingest import upsert_guild

    async with factory() as session, session.begin():
        await upsert_guild(session, GUILD)

    response = await logged_in.post(f"/api/discord/guilds/{GUILD.id}/load-threads")

    assert (response.status_code, response.json()["error"]["code"]) == (409, "account_not_found")
