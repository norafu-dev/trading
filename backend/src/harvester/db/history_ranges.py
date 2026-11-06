"""历史采集用到的纯函数（spec 0012）：时间和雪花 ID 的换算、进度。不碰数据库和时钟。"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

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


def reconcile_interval(
    before_id: int, returned_ids: tuple[int, ...], raw_count: int, range_start: datetime
) -> tuple[int, int]:
    """整段重抓时这一页要对账的 ID 区间 `[下界, 上界)`（AC-13）。

    上界是这一页请求的「之前」位置；到底的最后一页和空页下界是 `range_start` 对应的 ID，
    其余页的下界是这一页最旧一条消息的 ID（它自己返回了，不会被误标）。
    """
    floor = snowflake_floor(range_start)
    if page_reached_end(returned_ids, raw_count, range_start) or not returned_ids:
        return floor, before_id
    return min(returned_ids), before_id


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


# ---- 节奏和限额（AC-5） --------------------------------------------------------------

# 一个桶在窗口里最多多留不到 1 小时，再保守加 1 小时（偏保守，宁可晚一点继续）。
QUOTA_WINDOW = timedelta(hours=24)
QUOTA_RELEASE_DELAY = timedelta(hours=25)


def quota_resumes_at(buckets: Sequence[tuple[datetime, int]], limit: int) -> datetime | None:
    """用量达到上限时，最早可以继续的时间；没到上限返回 None。

    把窗口里的桶按 `hour` 从早到晚排，依次从用量里减掉每一桶；第一次让剩下的用量低于上限的
    那一桶，它的 `hour + 25 小时` 就是答案。
    """
    used = sum(pages for _, pages in buckets)
    if used < limit:
        return None
    for hour, pages in sorted(buckets):
        used -= pages
        if used < limit:
            return hour + QUOTA_RELEASE_DELAY
    return None


@dataclass(frozen=True)
class Pacing:
    """连续抓取的计时：累计时长、上一次计时的时刻、空闲开始的时刻、休息结束的时刻。"""

    ran: timedelta = timedelta(0)
    last_active: datetime | None = None
    idle_since: datetime | None = None
    rest_until: datetime | None = None


def pacing_working(pacing: Pacing, now: datetime, rest: timedelta) -> Pacing:
    """准备抓一页：把上次到现在的时间记进累计；从空闲回来时，空着超过休息时长也算休息过。"""
    if pacing.last_active is None:
        rested = pacing.idle_since is not None and now - pacing.idle_since >= rest
        return Pacing(ran=timedelta(0) if rested else pacing.ran, last_active=now)
    return replace(pacing, ran=pacing.ran + (now - pacing.last_active), last_active=now)


def pacing_idle(pacing: Pacing, now: datetime) -> Pacing:
    """没任务、暂停、离线或在等限额：不累计时间，记下空闲开始的时刻。"""
    return replace(pacing, last_active=None, idle_since=pacing.idle_since or now)


@dataclass(frozen=True)
class Proceed:
    """可以立即抓。"""


@dataclass(frozen=True)
class Rest:
    until: datetime


@dataclass(frozen=True)
class QuotaWait:
    until: datetime


Wait = Proceed | Rest | QuotaWait
WaitKind = Literal["rest", "quota"]


def next_wait(
    *,
    run: timedelta,
    rest: timedelta,
    daily_page_limit: int,
    pacing: Pacing,
    buckets: Sequence[tuple[datetime, int]],
    now: datetime,
) -> tuple[Wait, Pacing]:
    """下一步是立即抓、休息还是等限额，以及新的计时状态。

    休息结束时累计清零。`pacing` 里已经有休息结束时刻就沿用它，不会因为每次重新检查而一直顺延。
    """
    if pacing.rest_until is not None:
        if now < pacing.rest_until:
            return Rest(pacing.rest_until), pacing
        pacing = Pacing()
    if pacing.ran >= run:
        until = now + rest
        return Rest(until), replace(pacing, last_active=None, idle_since=now, rest_until=until)
    resumes = quota_resumes_at(buckets, daily_page_limit)
    if resumes is not None:
        return QuotaWait(resumes), pacing_idle(pacing, now)
    return Proceed(), pacing
