"""`ingest_message` against real PostgreSQL: insert, dedup, edits, attachments, forwards."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from harvester.db.ingest import ingest_message
from harvester.db.snapshots import EmbedData, ForwardedData, snowflake_time
from tests.db.builders import (
    BOT,
    CHANNEL,
    OTHER_GUILD,
    USER,
    WEBHOOK,
    at,
    attachment,
    count_messages,
    get_attachments,
    get_message,
    get_revisions,
    get_user,
    message,
    seed_channel,
    seed_other_channel,
    transaction_now,
)

RAW_EMBED = {"title": "BTC", "description": None, "author": {"name": "KOL", "url": None}}
CLEAN_EMBED: EmbedData = {"title": "BTC", "author": {"name": "KOL"}}
LINK: EmbedData = {"type": "link", "url": "https://x.example", "title": "Chart"}

FORWARDED: ForwardedData = {
    "message_id": 42,
    "channel_id": 43,
    "guild_id": None,
    "content": "original call",
    "embeds": [],
    "attachment_names": ["chart.png"],
}


@pytest.fixture(autouse=True)
async def channel(session: AsyncSession) -> None:
    await seed_channel(session)


# --- insert -----------------------------------------------------------------------


async def test_full_message_is_stored_as_given(session: AsyncSession) -> None:  # AC-2, AC-3
    snap = message(
        type=19,
        reply_to_id=1_299_999_999_999_999_999,
        embeds=(RAW_EMBED,),
        attachments=(attachment(1), attachment(2)),
    )

    result = await ingest_message(session, snap, "realtime", at(0))

    stored = await get_message(session)
    assert result == "inserted"
    assert (stored.channel_id, stored.guild_id, stored.author_id) == (CHANNEL.id, 100, USER.id)
    assert (stored.type, stored.content, stored.reply_to_id) == (
        19,
        "BTC long 65k",
        snap.reply_to_id,
    )
    assert stored.embeds == [CLEAN_EMBED]
    assert stored.created_at == snowflake_time(snap.id)
    assert (stored.version, stored.source, stored.edited_at) == (1, "realtime", None)
    assert [a.id for a in await get_attachments(session)] == [1, 2]
    assert (await get_user(session, USER.id)).username == "kol"


async def test_ingested_at_is_the_database_transaction_time(session: AsyncSession) -> None:
    await ingest_message(session, message(), "history", at(0))

    assert (await get_message(session)).ingested_at == await transaction_now(session)


async def test_message_without_embeds_stores_sql_null(session: AsyncSession) -> None:
    await ingest_message(session, message(embeds=({"title": None},)), "realtime", at(0))

    assert (await get_message(session)).embeds is None


async def test_message_edited_before_first_ingest_starts_at_version_one(  # value sourcing
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(edited_at=at(-5)), "history", at(0))

    stored = await get_message(session)
    assert (stored.version, stored.edited_at) == (1, at(-5))
    assert await get_revisions(session) == []


async def test_missing_channel_is_reported_without_writing(  # covers: AC-12
    session: AsyncSession,
) -> None:
    result = await ingest_message(session, message(channel_id=999), "realtime", at(0))

    assert result == "missing_channel"
    assert await count_messages(session) == 0


@pytest.mark.parametrize("message_type", [7, 20, 23])
async def test_other_message_types_are_skipped(  # covers: AC-4
    session: AsyncSession, message_type: int
) -> None:
    result = await ingest_message(session, message(type=message_type), "realtime", at(0))

    assert result == "skipped_type"
    assert await count_messages(session) == 0


async def test_guild_mismatch_is_rejected_by_the_composite_foreign_key(
    session: AsyncSession,
) -> None:
    await seed_other_channel(session)

    with pytest.raises(IntegrityError, match="fk_message_channel_id_guild_id_channel"):
        await ingest_message(session, message(guild_id=OTHER_GUILD.id), "realtime", at(0))


# --- dedup ------------------------------------------------------------------------


@pytest.mark.parametrize(("first", "second"), [("realtime", "history"), ("history", "realtime")])
async def test_same_message_from_two_sources_is_stored_once(  # covers: AC-2
    session: AsyncSession, first: str, second: str
) -> None:
    await ingest_message(session, message(), first, at(0))  # type: ignore[arg-type]
    ingested_at = (await get_message(session)).ingested_at
    await session.commit()

    result = await ingest_message(session, message(), second, at(5))  # type: ignore[arg-type]

    stored = await get_message(session)
    assert result == "unchanged"
    assert await count_messages(session) == 1
    assert (stored.source, stored.ingested_at) == (first, ingested_at)


async def test_concurrent_ingest_of_a_new_message_stores_one_row(  # covers: AC-2
    engine: AsyncEngine, session: AsyncSession
) -> None:
    await session.commit()  # make the seeded channel visible to the other sessions
    async with AsyncSession(engine) as first, AsyncSession(engine) as second:
        first_result = await ingest_message(first, message(), "realtime", at(0))
        # The second writer blocks on the first one's uncommitted row until it commits.
        second_task = asyncio.create_task(ingest_message(second, message(), "history", at(1)))
        await asyncio.sleep(0.2)
        await first.commit()
        second_result = await second_task
        await second.commit()

    assert (first_result, second_result) == ("inserted", "unchanged")
    assert await count_messages(session) == 1
    assert (await get_message(session)).source == "realtime"


# --- authors ----------------------------------------------------------------------


async def test_renamed_author_updates_user_but_not_old_messages(  # covers: AC-3
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))
    renamed = replace(USER, global_name="KOL v2")

    await ingest_message(
        session,
        message(id=1_300_000_000_000_000_001, author=renamed, author_display_name="KOL v2"),
        "realtime",
        at(1),
    )

    assert (await get_user(session, USER.id)).global_name == "KOL v2"
    assert (await get_message(session)).author_display_name == "KOL"


async def test_webhook_rename_keeps_the_first_username(  # covers: AC-3, AC-4
    session: AsyncSession,
) -> None:
    await ingest_message(
        session, message(author=WEBHOOK, author_display_name="TradingView"), "realtime", at(0)
    )
    await ingest_message(
        session,
        message(
            id=1_300_000_000_000_000_001,
            author=replace(WEBHOOK, username="Alerts"),
            author_display_name="Alerts",
        ),
        "realtime",
        at(1),
    )

    user = await get_user(session, WEBHOOK.id)
    assert (user.kind, user.username) == ("webhook", "TradingView")
    assert (await get_message(session, 1_300_000_000_000_000_001)).author_display_name == "Alerts"


async def test_bot_message_is_stored_with_bot_author(session: AsyncSession) -> None:  # AC-4
    result = await ingest_message(
        session, message(author=BOT, author_display_name="alertbot"), "realtime", at(0)
    )

    assert result == "inserted"
    assert (await get_user(session, BOT.id)).kind == "bot"


# --- edit table -------------------------------------------------------------------


async def test_real_edit_stores_old_content_as_a_revision(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(embeds=(LINK,)), "realtime", at(0))

    result = await ingest_message(
        session, message(content="BTC long 66k", edited_at=at(10)), "realtime", at(11)
    )

    stored = await get_message(session)
    [revision] = await get_revisions(session)
    assert result == "edited"
    assert (stored.content, stored.embeds, stored.edited_at, stored.version) == (
        "BTC long 66k",
        None,
        at(10),
        2,
    )
    assert (revision.version, revision.content, revision.embeds, revision.replaced_at) == (
        1,
        "BTC long 65k",
        [LINK],
        at(10),
    )


async def test_two_edits_give_version_three_and_two_revisions(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))
    await ingest_message(session, message(content="v2", edited_at=at(10)), "realtime", at(10))

    await ingest_message(session, message(content="v3", edited_at=at(20)), "realtime", at(20))

    revisions = await get_revisions(session)
    assert (await get_message(session)).version == 3
    assert [(r.version, r.content, r.replaced_at) for r in revisions] == [
        (1, "BTC long 65k", at(10)),
        (2, "v2", at(20)),
    ]


async def test_older_snapshot_is_stale_and_does_not_roll_back(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))
    await ingest_message(session, message(content="v2", edited_at=at(10)), "realtime", at(10))

    older = await ingest_message(
        session, message(content="v1.5", edited_at=at(5)), "history", at(30)
    )
    unedited = await ingest_message(session, message(), "history", at(31))

    stored = await get_message(session)
    assert (older, unedited) == ("stale", "stale")
    assert (stored.content, stored.edited_at, stored.version) == ("v2", at(10), 2)


async def test_newer_edit_time_with_same_content_only_moves_edited_at(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))

    result = await ingest_message(session, message(edited_at=at(10)), "realtime", at(10))

    stored = await get_message(session)
    assert result == "unchanged"
    assert (stored.edited_at, stored.version) == (at(10), 1)
    assert await get_revisions(session) == []


async def test_same_edit_time_with_new_content_is_an_edit_at_received_time(  # AC-5, AC-6
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(edited_at=at(5)), "realtime", at(5))

    result = await ingest_message(
        session, message(content="changed", edited_at=at(5)), "history", at(30)
    )

    [revision] = await get_revisions(session)
    assert result == "edited"
    assert (await get_message(session)).content == "changed"
    assert (revision.content, revision.replaced_at) == ("BTC long 65k", at(30))


async def test_history_finding_different_unedited_content_keeps_the_old_text(  # covers: AC-6
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))

    result = await ingest_message(session, message(content="backfilled"), "history", at(60))

    [revision] = await get_revisions(session)
    assert result == "edited"
    assert (revision.version, revision.content, revision.replaced_at) == (1, "BTC long 65k", at(60))


async def test_embed_unfurl_refreshes_embeds_without_a_new_version(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(), "realtime", at(0))

    result = await ingest_message(session, message(embeds=(LINK,)), "realtime", at(1))

    stored = await get_message(session)
    assert result == "embeds_refreshed"
    assert (stored.embeds, stored.version) == ([LINK], 1)
    assert await get_revisions(session) == []


async def test_identical_snapshot_is_unchanged(session: AsyncSession) -> None:  # AC-5
    await ingest_message(session, message(embeds=(RAW_EMBED,)), "realtime", at(0))

    result = await ingest_message(session, message(embeds=(CLEAN_EMBED,)), "realtime", at(1))

    assert result == "unchanged"
    assert (await get_message(session)).version == 1


# --- attachments ------------------------------------------------------------------


async def test_attachment_dropped_in_an_edit_is_marked_removed(  # covers: AC-5
    session: AsyncSession,
) -> None:
    await ingest_message(
        session, message(attachments=(attachment(1), attachment(2))), "realtime", at(0)
    )

    result = await ingest_message(
        session,
        message(edited_at=at(10), attachments=(attachment(1, url="https://cdn.example/new"),)),
        "realtime",
        at(11),
    )

    kept, dropped = await get_attachments(session)
    assert result == "unchanged"
    assert (await get_message(session)).version == 1
    assert (kept.removed_at, kept.url) == (None, "https://cdn.example/new")
    assert dropped.removed_at == at(10)


async def test_attachment_removed_without_edit_time_uses_received_at(
    session: AsyncSession,
) -> None:
    await ingest_message(
        session, message(attachments=(attachment(1), attachment(2))), "realtime", at(0)
    )

    await ingest_message(session, message(attachments=(attachment(1),)), "history", at(40))

    assert [a.removed_at for a in await get_attachments(session)] == [None, at(40)]


async def test_removed_at_is_not_moved_by_later_snapshots(session: AsyncSession) -> None:
    await ingest_message(
        session, message(attachments=(attachment(1), attachment(2))), "realtime", at(0)
    )
    await ingest_message(
        session, message(edited_at=at(10), attachments=(attachment(1),)), "realtime", at(10)
    )

    await ingest_message(
        session, message(edited_at=at(20), attachments=(attachment(1),)), "realtime", at(20)
    )

    assert (await get_attachments(session))[1].removed_at == at(10)


async def test_new_attachment_in_an_edit_is_added(session: AsyncSession) -> None:
    await ingest_message(session, message(attachments=(attachment(1),)), "realtime", at(0))

    await ingest_message(
        session,
        message(edited_at=at(10), attachments=(attachment(1), attachment(3))),
        "realtime",
        at(10),
    )

    assert [a.id for a in await get_attachments(session)] == [1, 3]


# --- forwarded --------------------------------------------------------------------


async def test_forwarded_content_is_written_once_and_never_changes(  # covers: AC-13
    session: AsyncSession,
) -> None:
    await ingest_message(session, message(forwarded=FORWARDED), "realtime", at(0))

    cleared = await ingest_message(session, message(forwarded=None), "history", at(1))
    other: ForwardedData = {**FORWARDED, "content": "something else"}
    changed = await ingest_message(session, message(forwarded=other), "history", at(2))

    assert (cleared, changed) == ("unchanged", "unchanged")
    assert (await get_message(session)).forwarded == FORWARDED
