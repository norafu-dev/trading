"""Unit tests for the pure rules in `harvester.db.snapshots` (no database)."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from harvester.db.snapshots import (
    BumpEditedAt,
    EmbedData,
    GuildSnapshot,
    NewVersion,
    NoWrite,
    RefreshEmbeds,
    StoredMessage,
    UpdateDecision,
    classify_update,
    normalize_embeds,
    snowflake_time,
)
from tests.db.builders import at

# --- snowflake_time ---------------------------------------------------------------


def test_snowflake_time_matches_the_discord_docs_example_to_the_millisecond() -> None:
    assert snowflake_time(175928847299117063) == datetime(
        2016, 4, 30, 11, 18, 25, 796000, tzinfo=UTC
    )


def test_snowflake_time_of_zero_is_the_discord_epoch() -> None:
    assert snowflake_time(0) == datetime(2015, 1, 1, tzinfo=UTC)


def test_snowflake_time_ignores_the_low_22_bits() -> None:
    base = 1_300_000_000_000_000_000 & ~((1 << 22) - 1)
    assert snowflake_time(base) == snowflake_time(base + (1 << 22) - 1)


def test_snowflake_time_keeps_milliseconds_for_large_ids() -> None:
    # A float based conversion would round this id's milliseconds.
    ms = 300_000_000_123
    assert snowflake_time(ms << 22).microsecond == 123_000


# --- normalize_embeds -------------------------------------------------------------


@pytest.mark.parametrize("embeds", [None, (), []])
def test_normalize_embeds_turns_missing_or_empty_into_none(embeds: object) -> None:
    assert normalize_embeds(embeds) is None  # type: ignore[arg-type]


def test_normalize_embeds_drops_none_values_recursively() -> None:  # covers: AC-3 (embeds)
    raw = [
        {
            "title": "BTC",
            "description": None,
            "author": {"name": "KOL", "url": None},
            "fields": [{"name": "Entry", "value": "65k", "inline": None}],
        }
    ]
    assert normalize_embeds(raw) == [
        {
            "title": "BTC",
            "author": {"name": "KOL"},
            "fields": [{"name": "Entry", "value": "65k"}],
        }
    ]


def test_normalize_embeds_drops_empty_lists_and_emptied_dicts() -> None:
    raw = [{"title": "x", "fields": [], "author": {"name": None}}]
    assert normalize_embeds(raw) == [{"title": "x"}]


def test_normalize_embeds_returns_none_when_every_embed_is_empty() -> None:
    assert normalize_embeds([{"title": None}, {"fields": []}]) is None


def test_normalize_embeds_keeps_falsy_scalars_that_are_not_none() -> None:
    raw = [
        {"color": 0, "description": "", "fields": [{"name": "a", "value": "b", "inline": False}]}
    ]
    assert normalize_embeds(raw) == raw


def test_normalize_embeds_is_idempotent() -> None:
    raw = [{"title": "x", "url": None, "fields": [{"name": "a", "value": "b"}]}]
    once = normalize_embeds(raw)
    assert normalize_embeds(once) == once


def test_normalize_embeds_does_not_mutate_its_input() -> None:
    raw = [{"title": "x", "url": None}]
    normalize_embeds(raw)
    assert raw == [{"title": "x", "url": None}]


# --- classify_update (spec 0002 edit table) ---------------------------------------

LINK: list[EmbedData] = [{"type": "link", "url": "https://x.example"}]


def stored(
    content: str = "old",
    embeds: list[EmbedData] | None = None,
    edited_at: datetime | None = None,
    deleted_at: datetime | None = None,
    version: int = 1,
) -> StoredMessage:
    return StoredMessage(
        content=content, embeds=embeds, edited_at=edited_at, deleted_at=deleted_at, version=version
    )


RECEIVED = at(30)


@pytest.mark.parametrize(
    ("db", "content", "embeds", "edited_at", "expected"),
    [
        # Row 1: deleted wins over everything, even a newer edit.
        (stored(deleted_at=at(5)), "new", None, at(10), NoWrite("deleted")),
        # Row 2: stale, snapshot has no edit time but the row does.
        (stored(edited_at=at(5)), "new", None, None, NoWrite("stale")),
        # Row 2: stale, snapshot edit time is older.
        (stored(edited_at=at(5)), "new", None, at(4), NoWrite("stale")),
        # Row 3: newer edit time (row never edited) with new content.
        (stored(), "new", None, at(10), NewVersion(replaced_at=at(10))),
        # Row 3: newer edit time with only embeds different still counts as an edit.
        (stored(edited_at=at(5)), "old", LINK, at(10), NewVersion(replaced_at=at(10))),
        # Row 4: newer edit time, same content and embeds.
        (stored(edited_at=at(5)), "old", None, at(10), BumpEditedAt()),
        # Row 5: same edit time (both empty), different content, replaced at received_at.
        (stored(), "new", None, None, NewVersion(replaced_at=RECEIVED)),
        # Row 5: same edit time (equal), different content.
        (stored(edited_at=at(5)), "new", None, at(5), NewVersion(replaced_at=RECEIVED)),
        # Row 6: same edit time, only embeds differ (link unfurl).
        (stored(), "old", LINK, None, RefreshEmbeds()),
        # Row 7: nothing differs.
        (stored(edited_at=at(5)), "old", None, at(5), NoWrite("unchanged")),
        (stored(), "old", None, None, NoWrite("unchanged")),
    ],
    ids=[
        "deleted",
        "stale-snapshot-unedited",
        "stale-older-edit",
        "edit-first-time",
        "edit-embeds-only-newer-time",
        "bump-edited-at",
        "same-time-none-content-differs",
        "same-time-equal-content-differs",
        "embeds-refreshed",
        "unchanged-equal-time",
        "unchanged-never-edited",
    ],
)
def test_classify_update_follows_the_edit_table(  # covers: AC-5, AC-6
    db: StoredMessage,
    content: str,
    embeds: list[EmbedData] | None,
    edited_at: datetime | None,
    expected: UpdateDecision,
) -> None:
    assert classify_update(db, content, embeds, edited_at, RECEIVED) == expected


def test_classify_update_treats_stored_embeds_in_raw_form_as_their_normal_form() -> None:
    # A row whose JSON still has a None key must not look different from the clean snapshot.
    db = stored(embeds=[{"title": "x", "url": None}])  # type: ignore[list-item]
    assert classify_update(db, "old", [{"title": "x"}], None, RECEIVED) == NoWrite("unchanged")


def test_classify_update_treats_empty_stored_embed_list_as_none() -> None:
    db = stored(embeds=[])
    assert classify_update(db, "old", None, None, RECEIVED) == NoWrite("unchanged")


def test_snapshots_are_frozen() -> None:  # covers: AC-12
    snap = GuildSnapshot(id=1, name="g", icon_hash=None)
    with pytest.raises(FrozenInstanceError):
        snap.name = "other"  # type: ignore[misc]
