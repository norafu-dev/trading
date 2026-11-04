"""历史采集用到的纯函数（spec 0012）：时间和雪花 ID 的换算、进度。不碰数据库和时钟。"""

import math
from datetime import datetime, timedelta

from harvester.db.snapshots import DISCORD_EPOCH, snowflake_time

# 一页最多 100 条；不满 100 条说明到底了（AC-3）。
PAGE_SIZE = 100
ONE_MS = timedelta(milliseconds=1)


def snowflake_floor(t: datetime) -> int:
    """时间点 `t` 对应的最小雪花 ID：早于 `t` 的消息 ID 都小于它，不早于 `t` 的都不小于它。

    消息时间精确到毫秒，所以 `t` 带微秒时向上取整到下一毫秒。
    """
    ms = math.ceil((t - DISCORD_EPOCH) / ONE_MS)
    return max(ms, 0) << 22


def page_cursor(returned_ids: tuple[int, ...], range_start: datetime) -> int | None:
    """这一页里不早于 `range_start` 的最旧一条消息 ID；没有就返回 None（AC-3）。"""
    floor = snowflake_floor(range_start)
    kept = [i for i in returned_ids if i >= floor]
    return min(kept) if kept else None


def page_reached_end(returned_ids: tuple[int, ...], raw_count: int, range_start: datetime) -> bool:
    """出现早于 `range_start` 的消息，或者 Discord 返回的原始条数不满一页，就算到底了（AC-3）。"""
    floor = snowflake_floor(range_start)
    return raw_count < PAGE_SIZE or any(i < floor for i in returned_ids)


def partial_coverage_start(cursor_message_id: int) -> datetime:
    """取消或失败时覆盖段的起点：断点时间加 1 毫秒，同一毫秒里更小的 ID 还没抓到（AC-11）。"""
    return snowflake_time(cursor_message_id) + ONE_MS


def progress(
    range_start: datetime, range_end: datetime, cursor_at: datetime | None, *, done: bool
) -> float:
    """`(range_end − 断点时间) / (range_end − range_start)`，限制在 0 到 1（AC-14）。

    没有断点是 0，`done` 是 1。
    """
    if done:
        return 1.0
    if cursor_at is None:
        return 0.0
    total = (range_end - range_start).total_seconds()
    if total <= 0:
        return 1.0
    return min(max((range_end - cursor_at).total_seconds() / total, 0.0), 1.0)
