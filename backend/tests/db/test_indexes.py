"""AC-11: newest 50 per channel, author and guild are index scans without a Sort."""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GUILDS = 10
CHANNELS = 50  # five per guild
AUTHORS = 200
MESSAGES = 100_000

SEED = [
    f"""
    INSERT INTO guild (id, name)
    SELECT g, 'guild ' || g FROM generate_series(1, {GUILDS}) g
    """,
    f"""
    INSERT INTO channel (id, guild_id, type, name)
    SELECT 1000 + c, 1 + c % {GUILDS}, 0, 'channel ' || c FROM generate_series(0, {CHANNELS - 1}) c
    """,
    f"""
    INSERT INTO discord_user (id, kind, username)
    SELECT 5000 + u, 'user', 'user ' || u FROM generate_series(0, {AUTHORS - 1}) u
    """,
    f"""
    INSERT INTO message
        (id, channel_id, guild_id, author_id, author_display_name, type, content,
         created_at, source)
    SELECT (n::bigint << 22),
           1000 + n % {CHANNELS},
           1 + (n % {CHANNELS}) % {GUILDS},
           5000 + n % {AUTHORS},
           'user',
           0,
           'message ' || n,
           now(),
           'history'
    FROM generate_series(1, {MESSAGES}) n
    """,
]

QUERIES = {
    "channel": "SELECT * FROM message WHERE channel_id = 1007 ORDER BY id DESC LIMIT 50",
    "author": "SELECT * FROM message WHERE author_id = 5042 ORDER BY id DESC LIMIT 50",
    "guild": "SELECT * FROM message WHERE guild_id = 3 ORDER BY id DESC LIMIT 50",
}


def node_types(plan: dict[str, Any]) -> list[str]:
    return [plan["Node Type"], *(t for child in plan.get("Plans", []) for t in node_types(child))]


@pytest.fixture
async def many_messages(session: AsyncSession) -> None:
    for statement in SEED:
        await session.execute(text(statement))
    await session.commit()
    await session.execute(text("ANALYZE"))
    await session.commit()


@pytest.mark.parametrize("dimension", QUERIES)
async def test_newest_page_uses_an_index_without_sorting(  # covers: AC-11
    session: AsyncSession, many_messages: None, dimension: str
) -> None:
    explain = await session.scalar(text(f"EXPLAIN (FORMAT JSON) {QUERIES[dimension]}"))
    assert explain is not None

    nodes = node_types(explain[0]["Plan"])

    assert any("Index" in n for n in nodes), nodes
    assert "Sort" not in nodes, nodes
