"""AC-6、AC-15：频道管理接口（真实 PostgreSQL）。"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harvester.db import channel_sync
from tests.api.conftest import FakeClock, complete_setup, do_login, make_client
from tests.db.builders import CATEGORY, CHANNEL, GUILD, at, make_account
from tests.db.test_channel_sync import STATE, VOICE

Factory = async_sessionmaker[AsyncSession]
ROUTES = (
    ("GET", "/api/discord/guilds"),
    ("GET", f"/api/discord/guilds/{GUILD.id}/channels"),
    ("PUT", f"/api/discord/channels/{CHANNEL.id}/collect"),
    ("PUT", f"/api/discord/categories/{CATEGORY.id}/collect"),
    ("GET", "/api/discord/collected"),
)


@pytest.fixture
async def logged_in(
    app: FastAPI, factory: Factory, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    async with make_client(app) as client:
        secret = await complete_setup(client, factory, clock)
        assert (await do_login(client, secret, clock)).status_code == 204
        yield client


async def seed(factory: Factory) -> None:
    async with factory() as session, session.begin():
        account_id = await make_account(session)
        await channel_sync.apply_guild_state(session, account_id, STATE, at(0))
        await channel_sync.record_synced(session, account_id, 1, at(0))


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_requires_login(client: httpx.AsyncClient, method: str, path: str) -> None:  # AC-15
    response = await client.request(method, path, json={"enabled": True})

    assert response.status_code == 401


async def test_cross_site_write_is_rejected(logged_in: httpx.AsyncClient) -> None:  # AC-15
    response = await logged_in.put(
        ROUTES[2][1], json={"enabled": True}, headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403


async def test_lists_guilds_and_channels_with_string_ids(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:
    await seed(factory)

    guilds = (await logged_in.get("/api/discord/guilds")).json()
    channels = (await logged_in.get(f"/api/discord/guilds/{GUILD.id}/channels")).json()

    assert guilds["synced_at"] is not None
    assert guilds["guilds"][0]["id"] == str(GUILD.id)
    row = next(c for c in channels["channels"] if c["id"] == str(CHANNEL.id))
    assert row["parent_id"] == "200"
    assert row["can_read"] is True and row["collectable"] is True


async def test_unknown_or_oversized_guild_is_404(logged_in: httpx.AsyncClient) -> None:
    for guild_id in ("404", "9999999999999999999"):
        response = await logged_in.get(f"/api/discord/guilds/{guild_id}/channels")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "guild_not_found"


async def test_toggle_round_trip(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-6
    await seed(factory)

    on = await logged_in.put(ROUTES[2][1], json={"enabled": True})
    again = await logged_in.put(ROUTES[2][1], json={"enabled": True})
    off = await logged_in.put(ROUTES[2][1], json={"enabled": False})

    assert (on.status_code, on.json()["collect_enabled"]) == (200, True)
    assert again.status_code == 200
    assert (off.status_code, off.json()["collect_enabled"]) == (200, False)


async def test_error_codes(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-6
    await seed(factory)

    voice = await logged_in.put(f"/api/discord/channels/{VOICE.id}/collect", json={"enabled": True})
    missing = await logged_in.put("/api/discord/channels/1/collect", json={"enabled": True})
    invalid = await logged_in.put(ROUTES[2][1], json={"enabled": "maybe"})

    assert (voice.status_code, voice.json()["error"]["code"]) == (409, "channel_not_collectable")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "channel_not_found")
    assert invalid.status_code == 422


async def test_category_collect_and_collected(
    logged_in: httpx.AsyncClient, factory: Factory
) -> None:  # AC-9, AC-13
    await seed(factory)

    bulk = await logged_in.put(ROUTES[3][1], json={"enabled": True})
    collected = (await logged_in.get("/api/discord/collected")).json()

    assert bulk.json() == {"changed": 1, "skipped_no_access": 0}
    assert collected["guilds"] == [{"id": "100", "name": "Signals", "icon_hash": "abc"}]
    assert {c["id"] for c in collected["channels"]} == {str(CHANNEL.id), str(CATEGORY.id)}


async def test_category_error_codes(logged_in: httpx.AsyncClient, factory: Factory) -> None:  # AC-9
    await seed(factory)

    missing = await logged_in.put("/api/discord/categories/1/collect", json={"enabled": True})
    wrong = await logged_in.put(
        f"/api/discord/categories/{CHANNEL.id}/collect", json={"enabled": True}
    )

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "category_not_found")
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (409, "not_a_category")
