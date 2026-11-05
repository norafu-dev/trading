"""spec 0012 的纯函数：时间和雪花 ID 换算、到底判断、进度。"""

from datetime import UTC, datetime, timedelta

from harvester.db.history_ranges import (
    Pacing,
    Proceed,
    QuotaWait,
    Rest,
    next_wait,
    pacing_idle,
    pacing_working,
    page_cursor,
    page_reached_end,
    partial_coverage_start,
    progress,
    quota_resumes_at,
    reconcile_interval,
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


# ---- 节奏和限额（AC-5） --------------------------------------------------------------

H = datetime(2026, 3, 1, tzinfo=UTC)
MIN = timedelta(minutes=1)


def test_quota_resumes_at_is_none_below_limit() -> None:
    assert quota_resumes_at([(H, 40), (H + timedelta(hours=1), 9)], 50) is None


def test_quota_resumes_when_earliest_bucket_frees_enough() -> None:
    buckets = [(H, 30), (H + timedelta(hours=1), 25)]
    assert quota_resumes_at(buckets, 50) == H + timedelta(hours=25)


def test_quota_resumes_at_next_bucket_when_first_is_not_enough() -> None:
    buckets = [(H, 5), (H + timedelta(hours=1), 5), (H + timedelta(hours=2), 60)]
    # 减掉前两桶后还剩 60，仍不低于 50，要等到第三桶也滑出窗口。
    assert quota_resumes_at(buckets, 50) == H + timedelta(hours=2) + timedelta(hours=25)


def test_quota_resumes_ignores_input_order() -> None:
    buckets = [(H + timedelta(hours=1), 25), (H, 30)]
    assert quota_resumes_at(buckets, 50) == H + timedelta(hours=25)


def wait_for(pacing: Pacing, now: datetime, *, buckets=(), limit: int = 1500):
    return next_wait(
        run=20 * MIN,
        rest=5 * MIN,
        daily_page_limit=limit,
        pacing=pacing,
        buckets=buckets,
        now=now,
    )


def test_proceeds_before_run_time_is_reached() -> None:
    wait, _ = wait_for(Pacing(ran=19 * MIN), H)
    assert wait == Proceed()


def test_rests_when_run_time_is_reached_and_keeps_rest_end() -> None:
    wait, pacing = wait_for(Pacing(ran=20 * MIN, last_active=H), H)
    assert wait == Rest(H + 5 * MIN)
    # 再次检查不会把休息顺延。
    again, _ = wait_for(pacing, H + 2 * MIN)
    assert again == Rest(H + 5 * MIN)


def test_rest_over_clears_the_running_total() -> None:
    _, pacing = wait_for(Pacing(ran=20 * MIN), H)
    wait, after = wait_for(pacing, H + 5 * MIN)
    assert wait == Proceed()
    assert after == Pacing()


def test_waits_for_quota_when_window_is_full() -> None:
    buckets = [(H - timedelta(hours=3), 1500)]
    wait, _ = wait_for(Pacing(), H, buckets=buckets)
    assert wait == QuotaWait(H - timedelta(hours=3) + timedelta(hours=25))


def test_pacing_accumulates_only_while_working() -> None:
    pacing = pacing_working(Pacing(), H, 5 * MIN)
    pacing = pacing_working(pacing, H + 3 * MIN, 5 * MIN)
    assert pacing.ran == 3 * MIN
    # 空闲的时间不累计。
    pacing = pacing_idle(pacing, H + 4 * MIN)
    pacing = pacing_working(pacing, H + 7 * MIN, 5 * MIN)
    assert pacing.ran == 3 * MIN


def test_idle_longer_than_rest_counts_as_rested() -> None:
    pacing = pacing_working(Pacing(), H, 5 * MIN)
    pacing = pacing_working(pacing, H + 19 * MIN, 5 * MIN)
    pacing = pacing_idle(pacing, H + 19 * MIN)
    assert pacing_working(pacing, H + 24 * MIN, 5 * MIN).ran == timedelta(0)
    short = pacing_working(pacing, H + 23 * MIN, 5 * MIN)
    assert short.ran == 19 * MIN


def test_reconcile_interval_middle_page_stops_at_oldest_returned() -> None:
    before = snowflake_floor(T)
    ids = (before - 5, before - 9, before - 12)

    assert reconcile_interval(before, ids, 100, T - timedelta(hours=1)) == (before - 12, before)


def test_reconcile_interval_last_and_empty_page_go_down_to_range_start() -> None:
    start = T - timedelta(hours=1)
    before = snowflake_floor(T)
    short = (before - 5,)

    assert reconcile_interval(before, short, 1, start) == (snowflake_floor(start), before)
    assert reconcile_interval(before, (), 0, start) == (snowflake_floor(start), before)
    # 满 100 条但其中有早于起点的：也是到底。
    older = (before - 5, snowflake_floor(start) - 1)
    assert reconcile_interval(before, older, 100, start) == (snowflake_floor(start), before)
