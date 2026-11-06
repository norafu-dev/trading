"""media 建行和重置（spec 0011 AC-2、AC-13）。"""

from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.models import Media
from harvester.db.snapshots import AttachmentSnapshot, EmbedData
from tests.db.builders import MESSAGE_ID, at, attachment, message, seed_channel

HOSTED = "https://cdn.discordapp.com/attachments/1/77/e.png?ex=1&hm=a"


def fwd(attachment_id: int) -> AttachmentSnapshot:
    return replace(
        attachment(attachment_id),
        url=f"https://cdn.discordapp.com/attachments/1/{attachment_id}/f.png",
    )


async def rows(session: AsyncSession) -> list[Media]:
    stmt = select(Media).order_by(Media.id).execution_options(populate_existing=True)
    return list(await session.scalars(stmt))


async def test_embed_rows_only_for_discord_hosted_urls(session: AsyncSession) -> None:  # AC-2
    await seed_channel(session)
    embeds: tuple[EmbedData, ...] = (
        {"image_url": HOSTED, "thumbnail_url": "https://example.com/t.png"},
        {"thumbnail_url": "https://media.discordapp.net/attachments/1/78/t.png"},
        {"image_url": "https://media.discordapp.net/external/x/y.png"},
    )

    await ingest_message(session, message(embeds=embeds), "realtime", at(0))
    await session.commit()

    got = await rows(session)
    assert [(r.kind, r.slot, r.filename, r.discord_attachment_id) for r in got] == [
        ("embed_image", 0, "e.png", 77),
        ("embed_thumbnail", 1, "t.png", 78),
    ]


async def test_forwarded_attachments_get_slots(session: AsyncSession) -> None:  # covers: AC-2
    await seed_channel(session)

    await ingest_message(
        session, message(forwarded_attachments=(fwd(1), fwd(2))), "realtime", at(0)
    )
    await session.commit()

    got = await rows(session)
    assert [(r.kind, r.slot, r.attachment_id) for r in got] == [
        ("forwarded_attachment", 0, None),
        ("forwarded_attachment", 1, None),
    ]


async def test_reingest_updates_link_but_keeps_one_row(session: AsyncSession) -> None:  # AC-1
    await seed_channel(session)
    first = message(
        attachments=(attachment(5, "https://cdn.discordapp.com/attachments/1/5/a?x=1"),)
    )
    again = message(
        attachments=(attachment(5, "https://cdn.discordapp.com/attachments/1/5/a?x=2"),)
    )

    await ingest_message(session, first, "realtime", at(0))
    await ingest_message(session, again, "history", at(1))
    await session.commit()

    [row] = await rows(session)
    assert row.source_url.endswith("x=2")


async def test_changed_embed_image_resets_the_row(session: AsyncSession) -> None:  # covers: AC-13
    await seed_channel(session)
    await ingest_message(session, message(embeds=({"image_url": HOSTED},)), "realtime", at(0))
    await session.execute(update(Media).values(status="failed", attempts=5, last_error="http_500"))
    await session.commit()
    swapped = "https://cdn.discordapp.com/attachments/1/79/other.png?ex=2"

    await ingest_message(
        session,
        message(content="edit", embeds=({"image_url": swapped},), edited_at=at(5)),
        "realtime",
        at(5),
    )
    await session.commit()

    [row] = await rows(session)
    assert (row.status, row.attempts, row.last_error, row.file_id) == ("pending", 0, None, None)
    assert row.source_url == swapped and row.filename == "other.png"


async def test_same_embed_image_with_new_signature_keeps_state(session: AsyncSession) -> None:
    await seed_channel(session)  # covers: AC-13
    await ingest_message(session, message(embeds=({"image_url": HOSTED},)), "realtime", at(0))
    await session.execute(update(Media).values(status="failed", attempts=5))
    await session.commit()

    await ingest_message(
        session,
        message(embeds=({"image_url": HOSTED.replace("hm=a", "hm=b")},), edited_at=at(1)),
        "realtime",
        at(1),
    )
    await session.commit()

    [row] = await rows(session)
    assert row.status == "failed" and row.source_url.endswith("hm=b")


async def test_deleted_message_and_removed_attachment_keep_media(  # covers: AC-13
    session: AsyncSession,
) -> None:
    await seed_channel(session)
    await ingest_message(session, message(attachments=(attachment(5),)), "realtime", at(0))
    await ingest_message(session, message(attachments=(), edited_at=at(2)), "realtime", at(2))
    await mark_messages_deleted(session, [MESSAGE_ID], at(3), "realtime")
    await session.commit()

    [row] = await rows(session)
    assert row.status == "pending" and row.attachment_id == 5


async def test_edit_without_attachments_key_does_not_touch_rows(  # covers: AC-13
    session: AsyncSession,
) -> None:
    await seed_channel(session)
    await ingest_message(session, message(attachments=(attachment(5),)), "realtime", at(0))
    await ingest_message(
        session,
        message(attachments=(), attachments_known=False, edited_at=at(2)),
        "realtime",
        at(2),
    )
    await session.commit()

    assert len(await rows(session)) == 1
