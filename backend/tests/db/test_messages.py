"""spec 0008：消息页读取和纯函数（真实 PostgreSQL）。"""

from dataclasses import replace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.ingest import ingest_message, mark_messages_deleted, upsert_user
from harvester.db.messages import (
    BIGINT_MAX,
    After,
    Before,
    ByIds,
    Latest,
    MessagePage,
    ReplyFound,
    ReplyMissing,
    extract_mentions,
    get_message_page,
    reply_preview,
)
from harvester.db.models import DiscordUser
from harvester.db.snapshots import UserSnapshot
from tests.db.builders import (
    BOT,
    CHANNEL,
    MESSAGE_ID,
    USER,
    at,
    attachment,
    get_attachments,
    message,
    seed_channel,
)

MENTIONED = UserSnapshot(id=400, kind="user", username="ann", global_name=None, avatar_hash="h")


async def page_of(session: AsyncSession, selector: object) -> MessagePage:
    result = await get_message_page(session, CHANNEL.id, selector)  # pyright: ignore[reportArgumentType]
    assert result != "channel_not_found"
    return result


async def seed_five(session: AsyncSession) -> list[int]:
    await seed_channel(session)
    ids = [MESSAGE_ID + i for i in range(5)]
    for message_id in ids:
        await ingest_message(session, message(id=message_id), "realtime", at(0))
    return ids


def ids_of(page: MessagePage) -> list[int]:
    return [m.id for m in page.messages]


async def test_latest_page_is_oldest_first_with_has_more(session: AsyncSession) -> None:  # AC-13
    ids = await seed_five(session)

    page = await page_of(session, Latest(2))

    assert ids_of(page) == ids[-2:]
    assert page.has_more


async def test_before_and_after_directions(session: AsyncSession) -> None:  # AC-13
    ids = await seed_five(session)

    older = await page_of(session, Before(ids[3], 2))
    assert ids_of(older) == ids[1:3]
    assert older.has_more

    newer = await page_of(session, After(ids[1], 2))
    assert ids_of(newer) == ids[2:4]
    assert newer.has_more

    assert not (await page_of(session, After(ids[1], 3))).has_more


async def test_ids_only_returns_this_channel_and_never_has_more(
    session: AsyncSession,
) -> None:  # AC-13
    ids = await seed_five(session)

    page = await page_of(session, ByIds((ids[2], ids[0], 999)))

    assert ids_of(page) == [ids[0], ids[2]]
    assert not page.has_more


async def test_missing_channel(session: AsyncSession) -> None:  # AC-13
    assert await get_message_page(session, 999, Latest(1)) == "channel_not_found"


async def test_deleted_messages_stay_in_the_page(session: AsyncSession) -> None:  # AC-13
    ids = await seed_five(session)
    await mark_messages_deleted(session, (ids[4],), at(1), "realtime")

    page = await page_of(session, Latest(1))

    assert page.messages[0].deleted_at == at(1)
    assert page.messages[0].content == "BTC long 65k"


async def test_edited_at_three_cases(session: AsyncSession) -> None:  # AC-14
    await seed_channel(session)
    await ingest_message(session, message(), "realtime", at(0))
    # version 1：embed 展开不算编辑。
    await ingest_message(session, message(embeds=({"title": "t"},)), "realtime", at(1))
    assert (await page_of(session, Latest(1))).messages[0].edited_at is None

    # 内容变了但 Discord 没给编辑时间：用旧版本的替换时间。
    await ingest_message(
        session, message(content="new", embeds=({"title": "t"},)), "realtime", at(2)
    )
    assert (await page_of(session, Latest(1))).messages[0].edited_at == at(2)

    await ingest_message(
        session,
        message(content="newer", embeds=({"title": "t"},), edited_at=at(3)),
        "realtime",
        at(4),
    )
    assert (await page_of(session, Latest(1))).messages[0].edited_at == at(3)


async def test_removed_attachments_are_filtered(session: AsyncSession) -> None:  # AC-14
    await seed_channel(session)
    snap = message(attachments=(attachment(1), attachment(2)))
    await ingest_message(session, snap, "realtime", at(0))
    await ingest_message(session, replace(snap, attachments=(attachment(1),)), "realtime", at(1))

    row = (await page_of(session, Latest(1))).messages[0]

    assert [a.id for a in row.attachments] == [1]


async def test_edit_without_attachments_key_keeps_them(session: AsyncSession) -> None:  # AC-4
    await seed_channel(session)
    snap = message(attachments=(attachment(1),))
    await ingest_message(session, snap, "realtime", at(0))

    await ingest_message(
        session, replace(snap, attachments=(), attachments_known=False), "realtime", at(1)
    )

    assert [a.removed_at for a in await get_attachments(session)] == [None]


async def test_reply_three_states(session: AsyncSession) -> None:  # AC-20
    await seed_channel(session)
    target = MESSAGE_ID
    await ingest_message(session, message(id=target, content="原文"), "realtime", at(0))
    await ingest_message(
        session, message(id=target + 1, type=19, reply_to_id=target), "realtime", at(0)
    )
    await ingest_message(session, message(id=target + 2, type=19, reply_to_id=5), "realtime", at(0))

    first, second = (await page_of(session, Latest(2))).messages
    assert first.reply == ReplyFound(target, "KOL", "原文", deleted=False)
    assert second.reply == ReplyMissing(5)

    await mark_messages_deleted(session, (target,), at(1), "realtime")
    first, _ = (await page_of(session, Latest(2))).messages
    assert first.reply == ReplyFound(target, "KOL", "原文", deleted=True)


@pytest.mark.parametrize(
    ("content", "embeds", "has_attachment", "forwarded", "expected"),
    [
        ("x" * 150, None, False, None, "x" * 100),
        ("", [{"title": "标题"}], True, None, "标题"),
        ("", None, True, None, "[附件]"),
        ("", None, False, {"content": ""}, "[转发]"),
        ("", [{"description": "d"}], False, None, "[无文字内容]"),
    ],
)
def test_reply_preview_fallbacks(
    content: str, embeds: object, has_attachment: bool, forwarded: object, expected: str
) -> None:  # AC-20
    assert reply_preview(content, embeds, has_attachment, forwarded) == expected  # pyright: ignore[reportArgumentType]


def test_extract_mentions_skips_roles_and_oversized_ids() -> None:  # AC-14
    users, channels = extract_mentions(
        ["<@1> <@!2> <@&3> <#4>", f"<@{BIGINT_MAX + 1}> <#99999999999999999999999>"]
    )

    assert users == {1, 2}
    assert channels == {4}


async def test_mentions_are_stored_and_resolved(session: AsyncSession) -> None:  # AC-6, AC-21
    await seed_channel(session)
    content = f"<@{MENTIONED.id}> <@{USER.id}> <#{CHANNEL.id}> <@&{BOT.id}>"
    await ingest_message(
        session, message(content=content, mentions=(MENTIONED, USER)), "realtime", at(0)
    )

    page = await page_of(session, Latest(1))

    assert {(u.id, u.name) for u in page.users} == {(MENTIONED.id, "ann"), (USER.id, "KOL")}
    assert [(c.id, c.name) for c in page.channels] == [(CHANNEL.id, CHANNEL.name)]


async def test_mention_never_overwrites_a_webhook_row(session: AsyncSession) -> None:  # AC-6
    await seed_channel(session)
    webhook = UserSnapshot(
        id=500, kind="webhook", username="hook", global_name=None, avatar_hash=None
    )
    await upsert_user(session, webhook)

    await ingest_message(
        session, message(mentions=(replace(webhook, kind="user", username="x"),)), "realtime", at(0)
    )

    row = (await session.execute(select(DiscordUser).where(DiscordUser.id == 500))).scalar_one()
    assert (row.kind, row.username) == ("webhook", "hook")


@pytest.mark.parametrize(
    "where",
    [
        f"channel_id = {CHANNEL.id}",
        f"channel_id = {CHANNEL.id} AND id < {MESSAGE_ID}",
        f"channel_id = {CHANNEL.id} AND id > {MESSAGE_ID}",
    ],
)
async def test_pages_use_the_index_without_sorting(session: AsyncSession, where: str) -> None:
    # 只要索引可用就不能排序（AC-13）。关掉 seqscan：测试库的表太小，规划器会偏向全表扫描。
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    order = "ASC" if ">" in where else "DESC"
    plan = await session.scalars(
        text(f"EXPLAIN SELECT id FROM message WHERE {where} ORDER BY id {order} LIMIT 51")
    )

    assert "Sort" not in "\n".join(plan)
