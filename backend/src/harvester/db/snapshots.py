"""Typed inputs and pure rules for the write functions in `harvester.db.ingest`.

The collector converts discord.py-self objects into these snapshots at its boundary;
nothing here imports the Discord library.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NotRequired, TypedDict, cast

# Value sets shared by the CHECK constraints in `models` and the function signatures.
AccountStatus = Literal[
    "connecting", "online", "reconnecting", "token_invalid", "banned", "disabled"
]
GuildStatus = Literal["active", "left", "deleted"]
ChannelStatus = Literal["active", "archived", "deleted"]
UserKind = Literal["user", "bot", "webhook"]
MessageSource = Literal["realtime", "history"]
DeleteSource = Literal["realtime", "reconciled"]
CoverageSource = Literal["realtime", "history"]
LoginOutcome = Literal[
    "success",
    "bad_credentials",
    "rate_limited",
    "setup_bad_code",
    "setup_completed",
    "logout",
    "reset",
]

# Text (0), announcement (5), announcement thread (10), public thread (11), private thread (12).
# Must match the `ck_channel_collectable_type` constraint; a test keeps the two in sync.
COLLECTABLE_CHANNEL_TYPES: tuple[int, ...] = (0, 5, 10, 11, 12)

# Default (0) and reply (19); every other message type is skipped.
STORED_MESSAGE_TYPES: tuple[int, ...] = (0, 19)

DISCORD_EPOCH = datetime(2015, 1, 1, tzinfo=UTC)


class EmbedAuthor(TypedDict):
    name: NotRequired[str]
    url: NotRequired[str]


class EmbedField(TypedDict):
    name: str
    value: str
    inline: NotRequired[bool]


class EmbedData(TypedDict):
    """The embed fields we keep; keys whose value would be None are left out."""

    type: NotRequired[str]
    title: NotRequired[str]
    description: NotRequired[str]
    url: NotRequired[str]
    color: NotRequired[int]
    timestamp: NotRequired[str]
    author: NotRequired[EmbedAuthor]
    fields: NotRequired[list[EmbedField]]
    footer_text: NotRequired[str]
    image_url: NotRequired[str]
    thumbnail_url: NotRequired[str]


class ForwardedData(TypedDict):
    message_id: int
    channel_id: int
    guild_id: int | None
    content: str
    embeds: list[EmbedData]
    attachment_names: list[str]


@dataclass(frozen=True)
class GuildSnapshot:
    id: int
    name: str
    icon_hash: str | None


@dataclass(frozen=True)
class ChannelSnapshot:
    id: int
    guild_id: int
    parent_id: int | None
    type: int
    name: str
    position: int | None


@dataclass(frozen=True)
class UserSnapshot:
    id: int
    kind: UserKind
    username: str
    global_name: str | None
    avatar_hash: str | None


@dataclass(frozen=True)
class AttachmentSnapshot:
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    url: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class MessageSnapshot:
    id: int
    channel_id: int
    guild_id: int
    author: UserSnapshot
    author_display_name: str
    type: int
    content: str
    embeds: tuple[EmbedData, ...]
    forwarded: ForwardedData | None
    reply_to_id: int | None
    edited_at: datetime | None
    attachments: tuple[AttachmentSnapshot, ...]


@dataclass(frozen=True)
class DeleteResult:
    marked: tuple[int, ...]
    already_deleted: tuple[int, ...]
    not_found: tuple[int, ...]


def snowflake_time(snowflake: int) -> datetime:
    """Creation time encoded in a Discord ID (integer math, no float rounding)."""
    return DISCORD_EPOCH + timedelta(milliseconds=snowflake >> 22)


def _drop_empty(value: object) -> object:
    """Recursively drop None values and empty lists; an emptied container becomes None."""
    if isinstance(value, Mapping):
        items = cast(Mapping[str, object], value).items()
        kept = {k: v for k, v in ((k, _drop_empty(v)) for k, v in items) if v is not None}
        return kept or None
    if isinstance(value, list | tuple):
        cleaned = (_drop_empty(v) for v in cast(Sequence[object], value))
        return [v for v in cleaned if v is not None] or None
    return value


def normalize_embeds(embeds: Sequence[Mapping[str, object]] | None) -> list[EmbedData] | None:
    """The one canonical embed form, used both for storage and for edit comparison."""
    if not embeds:
        return None
    normalized = [e for e in (_drop_empty(e) for e in embeds) if e is not None]
    return cast(list[EmbedData], normalized) or None


@dataclass(frozen=True)
class StoredMessage:
    """The columns of an existing `message` row that the edit table looks at."""

    content: str
    embeds: list[EmbedData] | None
    edited_at: datetime | None
    deleted_at: datetime | None
    version: int


@dataclass(frozen=True)
class NoWrite:
    outcome: Literal["deleted", "stale", "unchanged"]


@dataclass(frozen=True)
class NewVersion:
    """A real edit: store the old content as a revision replaced at `replaced_at`."""

    replaced_at: datetime


@dataclass(frozen=True)
class RefreshEmbeds:
    """Same edit time, only the embeds changed (link unfurl): update them in place."""


@dataclass(frozen=True)
class BumpEditedAt:
    """Newer edit time with identical content (e.g. an attachment removed)."""


UpdateDecision = NoWrite | NewVersion | RefreshEmbeds | BumpEditedAt


def classify_update(
    stored: StoredMessage,
    content: str,
    embeds: list[EmbedData] | None,
    edited_at: datetime | None,
    received_at: datetime,
) -> UpdateDecision:
    """The edit table from spec 0002, first matching row wins. `embeds` must be normalized."""
    if stored.deleted_at is not None:
        return NoWrite("deleted")
    if stored.edited_at is not None and (edited_at is None or edited_at < stored.edited_at):
        return NoWrite("stale")

    content_differs = content != stored.content
    embeds_differ = embeds != normalize_embeds(stored.embeds)
    if edited_at is not None and (stored.edited_at is None or edited_at > stored.edited_at):
        if content_differs or embeds_differ:
            return NewVersion(replaced_at=edited_at)
        return BumpEditedAt()
    # Same edit time from here on (both empty, or equal).
    if content_differs:
        return NewVersion(replaced_at=received_at)
    if embeds_differ:
        return RefreshEmbeds()
    return NoWrite("unchanged")
