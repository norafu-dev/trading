"""spec 0012 的纯函数：时间和雪花 ID 换算、到底判断、进度。"""

from datetime import UTC, datetime, timedelta

from harvester.db.history_ranges import (
    page_cursor,
    page_reached_end,
    partial_coverage_start,
    progress,
    snowflake_floor,
)
from harvester.db.snapshots import DISCORD_EPOCH, snowflake_time

T = datetime(2026, 3, 1, 8, 30, tzinfo=UTC)


def test_snowflake_floor_round_trips_whole_milliseconds() -> None:
    floor = snowflake_floor(T)
    assert snowflake_time(floor) == T
    assert snowflake_time(floor - 1) < T


def test_snowflake_floor_rounds_microseconds_up() -> None:
    t = T + timedelta(microseconds=400)
    floor = snowflake_floor(t)
    # 同一毫秒里更早的消息算早于 t，所以下界是下一毫秒。
    assert snowflake_time(floor) == T + timedelta(milliseconds=1)
    assert snowflake_time(floor - 1) < t


def test_snowflake_floor_never_negative() -> None:
    assert snowflake_floor(DISCORD_EPOCH - timedelta(days=1)) == 0


def test_page_reached_end_on_short_page_or_older_message() -> None:
    inside = snowflake_floor(T) + 10
    older = snowflake_floor(T) - 1
    full = tuple(inside + i for i in range(100))
    assert not page_reached_end(full, 100, T)
    assert page_reached_end(full[:99], 99, T)
    assert page_reached_end((*full[:99], older), 100, T)
    assert page_reached_end((), 0, T)


def test_page_cursor_is_oldest_message_not_before_start() -> None:
    floor = snowflake_floor(T)
    assert page_cursor((floor + 9, floor + 3, floor - 5), T) == floor + 3
    assert page_cursor((floor - 5,), T) is None


def test_partial_coverage_starts_one_ms_after_cursor() -> None:
    cursor = snowflake_floor(T) + 77
    assert partial_coverage_start(cursor) == T + timedelta(milliseconds=1)


def test_progress() -> None:
    start, end = T, T + timedelta(hours=10)
    assert progress(start, end, None, done=False) == 0.0
    assert progress(start, end, end - timedelta(hours=4), done=False) == 0.4
    assert progress(start, end, None, done=True) == 1.0
    assert progress(start, end, start - timedelta(hours=1), done=False) == 1.0
