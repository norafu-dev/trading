"""Write functions from spec 0002. None of them commits: one Discord event is one
transaction, owned by the caller (NOTIFY goes in the same transaction, spec 0001).

Expected outcomes are returned as Literals or frozen dataclasses; only a genuinely
unexpected database error raises.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, cast

from sqlalchemy import Table, and_, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import Range, insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.media import sync_media_for_message
from harvester.db.models import (
    Attachment,
    Channel,
    ChannelCoverage,
    DiscordUser,
    Guild,
    Message,
    MessageRevision,
)
from harvester.db.snapshots import (
    STORED_MESSAGE_TYPES,
    AttachmentSnapshot,
    BumpEditedAt,
    ChannelSnapshot,
    ChannelStatus,
    DeleteResult,
    DeleteSource,
    EmbedData,
    GuildSnapshot,
    GuildStatus,
    MessageSnapshot,
    MessageSource,
    NewVersion,
    NoWrite,
    RefreshEmbeds,
    StoredMessage,
    UserSnapshot,
    classify_update,
    normalize_embeds,
    snowflake_time,
)

UpsertChannelResult = Literal["ok", "missing_guild", "missing_parent"]
StatusResult = Literal["changed", "unchanged", "not_found"]
IngestResult = Literal[
    "inserted",
    "edited",
    "embeds_refreshed",
    "unchanged",
    "stale",
    "deleted",
    "skipped_type",
    "missing_channel",
]
OpenCoverageResult = Literal["opened", "already_open"]
CloseCoverageResult = Literal["closed", "discarded_empty", "not_open"]

# `__table__` is typed as a plain FromClause; these are the same objects, typed as Tables.
_GUILD = cast(Table, Guild.__table__)
_CHANNEL = cast(Table, Channel.__table__)
_DISCORD_USER = cast(Table, DiscordUser.__table__)
_ATTACHMENT = cast(Table, Attachment.__table__)


async def _upsert(
    session: AsyncSession,
    table: Table,
    values: Mapping[str, object],
    update_columns: Sequence[str],
    extra_where: str | None = None,
) -> None:
    """INSERT ... ON CONFLICT (id) DO UPDATE only the given columns, and only when they differ."""
    stmt = insert(table).values(dict(values))
    changed = or_(*(table.c[c].is_distinct_from(stmt.excluded[c]) for c in update_columns))
    where = changed if extra_where is None else and_(text(extra_where), changed)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={**{c: stmt.excluded[c] for c in update_columns}, "updated_at": func.now()},
            where=where,
        )
    )


async def _row_exists(session: AsyncSession, table: Table, row_id: int) -> bool:
    return bool(await session.scalar(select(exists().where(table.c.id == row_id))))


async def upsert_guild(session: AsyncSession, snap: GuildSnapshot) -> None:
    await _upsert(
        session,
        _GUILD,
        {"id": snap.id, "name": snap.name, "icon_hash": snap.icon_hash},
        ("name", "icon_hash"),
    )


async def upsert_channel(session: AsyncSession, snap: ChannelSnapshot) -> UpsertChannelResult:
    if not await _row_exists(session, _GUILD, snap.guild_id):
        return "missing_guild"
    if snap.parent_id is not None and not await _row_exists(session, _CHANNEL, snap.parent_id):
        return "missing_parent"
    await _upsert(
        session,
        _CHANNEL,
        {
            "id": snap.id,
            "guild_id": snap.guild_id,
            "parent_id": snap.parent_id,
            "type": snap.type,
            "name": snap.name,
            "position": snap.position,
        },
        ("name", "type", "parent_id", "position"),
    )
    return "ok"


async def _set_status(
    session: AsyncSession, table: Table, row_id: int, status: str, at: datetime
) -> StatusResult:
    changed = await session.scalar(
        update(table)
        .where(table.c.id == row_id, table.c.status.is_distinct_from(status))
        .values(status=status, status_changed_at=at, updated_at=func.now())
        .returning(table.c.id)
    )
    if changed is not None:
        return "changed"
    return "unchanged" if await _row_exists(session, table, row_id) else "not_found"


async def set_guild_status(
    session: AsyncSession, guild_id: int, status: GuildStatus, at: datetime
) -> StatusResult:
    return await _set_status(session, _GUILD, guild_id, status, at)


async def set_channel_status(
    session: AsyncSession, channel_id: int, status: ChannelStatus, at: datetime
) -> StatusResult:
    """Never touches the channel's messages: a deleted channel keeps them as they were."""
    return await _set_status(session, _CHANNEL, channel_id, status, at)


async def upsert_user(session: AsyncSession, snap: UserSnapshot) -> None:
    """`kind` is set on first insert only; webhook rows are never updated."""
    await _upsert(
        session,
        _DISCORD_USER,
        {
            "id": snap.id,
            "kind": snap.kind,
            "username": snap.username,
            "global_name": snap.global_name,
            "avatar_hash": snap.avatar_hash,
        },
        ("username", "global_name", "avatar_hash"),
        extra_where="discord_user.kind <> 'webhook'",
    )


async def _lock_message(session: AsyncSession, message_id: int) -> StoredMessage | None:
    row = (
        await session.execute(
            select(
                Message.content,
                Message.embeds,
                Message.edited_at,
                Message.deleted_at,
                Message.version,
            )
            .where(Message.id == message_id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return None
    return StoredMessage(
        content=row.content,
        embeds=row.embeds,
        edited_at=row.edited_at,
        deleted_at=row.deleted_at,
        version=row.version,
    )


async def _sync_attachments(
    session: AsyncSession,
    message_id: int,
    attachments: Sequence[AttachmentSnapshot],
    removed_at: datetime | None,
) -> None:
    """Upsert the snapshot's attachments; with `removed_at`, mark the missing ones removed."""
    if attachments:
        table = _ATTACHMENT
        stmt = insert(table).values(
            [
                {
                    "id": a.id,
                    "message_id": message_id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size_bytes": a.size_bytes,
                    "url": a.url,
                    "width": a.width,
                    "height": a.height,
                }
                for a in attachments
            ]
        )
        mutable = ("url", "content_type", "width", "height")
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[table.c.id],
                set_={c: stmt.excluded[c] for c in mutable},
                where=or_(*(table.c[c].is_distinct_from(stmt.excluded[c]) for c in mutable)),
            )
        )
    if removed_at is not None:
        await session.execute(
            update(Attachment)
            .where(
                Attachment.message_id == message_id,
                Attachment.removed_at.is_(None),
                Attachment.id.not_in([a.id for a in attachments]),
            )
            .values(removed_at=removed_at)
        )


async def _insert_message(
    session: AsyncSession,
    snap: MessageSnapshot,
    embeds: list[EmbedData] | None,
    source: MessageSource,
) -> bool:
    """True if this call inserted the row; False if a concurrent writer got there first."""
    inserted = await session.scalar(
        insert(Message)
        .values(
            id=snap.id,
            channel_id=snap.channel_id,
            guild_id=snap.guild_id,
            author_id=snap.author.id,
            author_display_name=snap.author_display_name,
            type=snap.type,
            content=snap.content,
            embeds=embeds,
            forwarded=snap.forwarded,
            reply_to_id=snap.reply_to_id,
            created_at=snowflake_time(snap.id),
            edited_at=snap.edited_at,
            source=source,
        )
        .on_conflict_do_nothing(index_elements=[Message.id])
        .returning(Message.id)
    )
    return inserted is not None


async def ingest_message(
    session: AsyncSession,
    snap: MessageSnapshot,
    source: MessageSource,
    received_at: datetime,
) -> IngestResult:
    """Store a message once, whether it arrives live or from history (spec 0002 flow)."""
    if snap.type not in STORED_MESSAGE_TYPES:
        return "skipped_type"
    if not await _row_exists(session, _CHANNEL, snap.channel_id):
        return "missing_channel"
    await upsert_user(session, snap.author)
    for mentioned in sorted(snap.mentions, key=lambda user: user.id):
        if mentioned.id != snap.author.id:
            await upsert_user(session, mentioned)

    embeds = normalize_embeds(snap.embeds)
    stored = await _lock_message(session, snap.id)
    if stored is None:
        if await _insert_message(session, snap, embeds, source):
            if snap.attachments_known:
                await _sync_attachments(session, snap.id, snap.attachments, removed_at=None)
            await sync_media_for_message(session, snap, embeds)
            return "inserted"
        stored = await _lock_message(session, snap.id)
        if stored is None:  # The conflicting insert committed, so the row must be there now.
            raise RuntimeError(f"message {snap.id} vanished after an insert conflict")

    outcome: IngestResult
    match classify_update(stored, snap.content, embeds, snap.edited_at, received_at):
        case NoWrite(outcome="deleted" | "stale" as skipped):
            return skipped
        case NoWrite():
            outcome = "unchanged"
        case NewVersion(replaced_at=replaced_at):
            session.add(
                MessageRevision(
                    message_id=snap.id,
                    version=stored.version,
                    content=stored.content,
                    embeds=stored.embeds,
                    replaced_at=replaced_at,
                )
            )
            await session.execute(
                update(Message)
                .where(Message.id == snap.id)
                .values(
                    content=snap.content,
                    embeds=embeds,
                    edited_at=snap.edited_at,
                    version=Message.version + 1,
                )
            )
            outcome = "edited"
        case RefreshEmbeds():
            await session.execute(
                update(Message).where(Message.id == snap.id).values(embeds=embeds)
            )
            outcome = "embeds_refreshed"
        case BumpEditedAt():
            await session.execute(
                update(Message).where(Message.id == snap.id).values(edited_at=snap.edited_at)
            )
            outcome = "unchanged"

    if snap.attachments_known:
        await _sync_attachments(
            session, snap.id, snap.attachments, removed_at=snap.edited_at or received_at
        )
    await sync_media_for_message(session, snap, embeds)
    await session.flush()
    return outcome


async def mark_messages_deleted(
    session: AsyncSession,
    ids: Sequence[int],
    at: datetime,
    source: DeleteSource,
) -> DeleteResult:
    """Flag messages deleted; the first deletion time is never overwritten."""
    unique_ids = tuple(dict.fromkeys(ids))
    if not unique_ids:
        return DeleteResult(marked=(), already_deleted=(), not_found=())
    marked = set(
        await session.scalars(
            update(Message)
            .where(Message.id.in_(unique_ids), Message.deleted_at.is_(None))
            .values(deleted_at=at, delete_source=source)
            .returning(Message.id)
        )
    )
    known = set(await session.scalars(select(Message.id).where(Message.id.in_(unique_ids))))
    return DeleteResult(
        marked=tuple(i for i in unique_ids if i in marked),
        already_deleted=tuple(i for i in unique_ids if i in known and i not in marked),
        not_found=tuple(i for i in unique_ids if i not in known),
    )


_OPEN_REALTIME = "source = 'realtime' AND upper_inf(span)"


async def open_realtime_coverage(
    session: AsyncSession, channel_id: int, at: datetime
) -> OpenCoverageResult:
    opened = await session.scalar(
        insert(ChannelCoverage)
        .values(channel_id=channel_id, span=Range(at, None, bounds="[)"), source="realtime")
        .on_conflict_do_nothing(index_elements=["channel_id"], index_where=text(_OPEN_REALTIME))
        .returning(ChannelCoverage.id)
    )
    return "opened" if opened is not None else "already_open"


async def close_realtime_coverage(
    session: AsyncSession, channel_id: int, at: datetime
) -> CloseCoverageResult:
    """Close at `max(at, start)`; a segment that would be empty is deleted instead."""
    row = (
        await session.execute(
            select(ChannelCoverage.id, ChannelCoverage.span)
            .where(ChannelCoverage.channel_id == channel_id, text(_OPEN_REALTIME))
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return "not_open"
    start = row.span.lower
    if start is None or at <= start:
        await session.execute(delete(ChannelCoverage).where(ChannelCoverage.id == row.id))
        return "discarded_empty"
    await session.execute(
        update(ChannelCoverage)
        .where(ChannelCoverage.id == row.id)
        .values(span=Range(start, at, bounds="[)"))
    )
    return "closed"


# Segments left open by a crashed collector: its own channels, unowned ones, and disabled ones.
# Closed at the account's last heartbeat (never before the start); empty results are deleted.
_CLOSE_STALE_SQL = text(
    """
    WITH stale AS (
        SELECT cc.id,
               lower(cc.span) AS lo,
               GREATEST(
                   coalesce(
                       (SELECT last_heartbeat_at FROM discord_account WHERE id = :account_id),
                       lower(cc.span)
                   ),
                   lower(cc.span)
               ) AS hi
        FROM channel_coverage cc
        JOIN channel c ON c.id = cc.channel_id
        WHERE cc.source = 'realtime'
          AND upper_inf(cc.span)
          AND (c.collector_account_id = :account_id
               OR c.collector_account_id IS NULL
               OR NOT c.collect_enabled)
        FOR UPDATE OF cc
    ),
    discarded AS (
        DELETE FROM channel_coverage
        WHERE id IN (SELECT id FROM stale WHERE hi <= lo)
        RETURNING id
    ),
    closed AS (
        UPDATE channel_coverage cc
        SET span = tstzrange(s.lo, s.hi, '[)')
        FROM stale s
        WHERE cc.id = s.id AND s.hi > s.lo
        RETURNING cc.id
    )
    SELECT (SELECT count(*) FROM discarded) + (SELECT count(*) FROM closed)
    """
)


async def close_stale_coverage(session: AsyncSession, account_id: int) -> int:
    """Run at collector start-up; returns how many open segments were closed or discarded."""
    return int(await session.scalar(_CLOSE_STALE_SQL, {"account_id": account_id}) or 0)
