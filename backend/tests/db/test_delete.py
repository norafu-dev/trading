"""`mark_messages_deleted`: soft deletes only, the first deletion wins (real PostgreSQL)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.snapshots import DeleteResult, ForwardedData
from tests.db.builders import (
    MESSAGE_ID,
    at,
    attachment,
    get_attachments,
    get_message,
    get_revisions,
    message,
    seed_channel,
)

LIVE = MESSAGE_ID
GONE = MESSAGE_ID + 1
MISSING = MESSAGE_ID + 99

FORWARDED: ForwardedData = {
    "message_id": 42,
    "channel_id": 43,
    "guild_id": 44,
    "content": "fwd",
    "embeds": [],
    "attachment_names": [],
}


@pytest.fixture(autouse=True)
async def two_messages(session: AsyncSession) -> None:
    """LIVE has an embed, a forward, an attachment and one revision; GONE is already deleted."""
    await seed_channel(session)
    await ingest_message(
        session,
        message(
            id=LIVE,
            embeds=({"title": "x"},),
            forwarded=FORWARDED,
            attachments=(attachment(1),),
        ),
        "realtime",
        at(0),
    )
    await ingest_message(
        session,
        message(
            id=LIVE,
            content="v2",
            edited_at=at(1),
            embeds=({"title": "x"},),
            attachments=(attachment(1),),
        ),
        "realtime",
        at(1),
    )
    await ingest_message(session, message(id=GONE), "realtime", at(0))
    await mark_messages_deleted(session, (GONE,), at(2), "realtime")


async def test_batch_delete_sorts_ids_into_three_outcomes(  # covers: AC-7
    session: AsyncSession,
) -> None:
    result = await mark_messages_deleted(session, (LIVE, GONE, MISSING), at(5), "realtime")

    assert result == DeleteResult(marked=(LIVE,), already_deleted=(GONE,), not_found=(MISSING,))


async def test_delete_records_the_given_time_and_source(session: AsyncSession) -> None:  # AC-7
    await mark_messages_deleted(session, (LIVE,), at(5), "reconciled")

    stored = await get_message(session, LIVE)
    assert (stored.deleted_at, stored.delete_source) == (at(5), "reconciled")


async def test_second_delete_keeps_the_first_time_and_source(  # covers: AC-7
    session: AsyncSession,
) -> None:
    await mark_messages_deleted(session, (GONE,), at(9), "reconciled")

    stored = await get_message(session, GONE)
    assert (stored.deleted_at, stored.delete_source) == (at(2), "realtime")


async def test_delete_keeps_content_embeds_forward_attachments_and_revisions(  # covers: AC-7
    session: AsyncSession,
) -> None:
    await mark_messages_deleted(session, (LIVE,), at(5), "realtime")

    stored = await get_message(session, LIVE)
    assert (stored.content, stored.embeds, stored.forwarded) == ("v2", [{"title": "x"}], FORWARDED)
    assert stored.version == 2
    assert [(r.version, r.content) for r in await get_revisions(session, LIVE)] == [
        (1, "BTC long 65k")
    ]
    assert [(a.id, a.removed_at) for a in await get_attachments(session, LIVE)] == [(1, None)]


async def test_edit_after_delete_is_ignored(session: AsyncSession) -> None:  # covers: AC-7
    await mark_messages_deleted(session, (LIVE,), at(5), "realtime")

    result = await ingest_message(
        session, message(id=LIVE, content="v3", edited_at=at(6), attachments=()), "realtime", at(6)
    )

    stored = await get_message(session, LIVE)
    assert result == "deleted"
    assert (stored.content, stored.version) == ("v2", 2)
    assert (await get_attachments(session, LIVE))[0].removed_at is None


async def test_duplicate_ids_are_reported_once(session: AsyncSession) -> None:
    result = await mark_messages_deleted(session, (LIVE, LIVE, MISSING, MISSING), at(5), "realtime")

    assert result == DeleteResult(marked=(LIVE,), already_deleted=(), not_found=(MISSING,))


async def test_empty_batch_returns_empty_result(session: AsyncSession) -> None:
    assert await mark_messages_deleted(session, (), at(5), "realtime") == DeleteResult(
        marked=(), already_deleted=(), not_found=()
    )
