"""睡眠保护的纯函数和等待逻辑（spec 0007），全部用假时钟。"""

import asyncio

import pytest
from structlog.testing import CapturingLogger

from harvester.collector import wake_guard
from harvester.collector.wake_guard import (
    SETTLE_SECONDS,
    WATCH_INTERVAL_SECONDS,
    WakeGuard,
    WakeState,
    observe,
    seconds_until_settled,
    watch_sleep,
)

BASE = observe(WakeState(), 1000.0, 50.0)


def test_first_sample_only_sets_the_baseline() -> None:  # covers: AC-1
    assert WakeState(1000.0, 50.0) == BASE
    assert seconds_until_settled(BASE, 50.0) == 0


@pytest.mark.parametrize(
    ("gap", "slept"), [(9.0, False), (10.0, False), (11.0, True), (-3600.0, False)]
)
def test_only_a_gap_above_the_limit_counts(gap: float, slept: bool) -> None:  # covers: AC-1
    state = observe(BASE, 1005.0 + gap, 55.0)

    assert (state.slept_mono == 55.0) is slept
    assert state.gap_seconds == (gap if slept else None)


def test_later_samples_keep_the_sleep_but_not_the_gap() -> None:  # covers: AC-1
    slept = observe(BASE, 2000.0, 55.0)
    later = observe(slept, 2005.0, 60.0)

    assert later.slept_mono == 55.0
    assert later.gap_seconds is None


def test_settling_counts_from_the_latest_sleep() -> None:  # covers: AC-2
    slept = observe(BASE, 2000.0, 100.0)

    assert seconds_until_settled(slept, 100.0 + 89) == 1
    assert seconds_until_settled(slept, 100.0 + 90) == 0

    again = observe(slept, 3000.0, 145.0)
    assert seconds_until_settled(again, 145.0 + 89) == 1
    assert seconds_until_settled(again, 145.0 + 90) == 0


class FakeTime:
    """sleep 让两个时钟一起走；到 suspend_at 时再模拟一次睡眠。"""

    def __init__(self, suspend_at: float | None = None) -> None:
        self.wall = 1000.0
        self.mono = 0.0
        self.suspend_at = suspend_at
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.mono += seconds
        self.wall += seconds
        if self.suspend_at is not None and self.mono >= self.suspend_at:
            self.wall += 3600
            self.suspend_at = None

    def guard(self) -> WakeGuard:
        return WakeGuard(lambda: self.wall, lambda: self.mono, self.sleep)


async def test_wait_returns_at_once_without_sleep() -> None:  # covers: AC-4
    clock = FakeTime()
    guard = clock.guard()

    await guard.wait_until_settled()

    assert clock.sleeps == []


async def test_wait_notices_sleep_it_was_not_told_about() -> None:  # covers: AC-2
    clock = FakeTime()
    guard = clock.guard()
    guard.sample()
    clock.wall += 3600  # 没有别的采样，靠 wait_until_settled 自己发现。

    await guard.wait_until_settled()

    assert sum(clock.sleeps) == SETTLE_SECONDS
    assert max(clock.sleeps) == WATCH_INTERVAL_SECONDS


async def test_sleeping_again_restarts_the_wait() -> None:  # covers: AC-2
    clock = FakeTime(suspend_at=45)
    guard = clock.guard()
    guard.sample()
    clock.wall += 3600

    await guard.wait_until_settled()

    assert clock.mono == 45 + SETTLE_SECONDS


def test_sleep_is_logged_once_with_its_length(monkeypatch: pytest.MonkeyPatch) -> None:  # AC-1
    logger = CapturingLogger()
    monkeypatch.setattr(wake_guard, "log", logger)
    clock = FakeTime()
    guard = clock.guard()

    guard.sample()
    assert logger.calls == []
    clock.wall += 3600
    guard.sample()
    guard.sample()

    assert [(c.args, c.kwargs) for c in logger.calls] == [
        (("collector.sleep_detected",), {"gap_seconds": 3600.0})
    ]


async def test_watch_sleep_samples_until_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = CapturingLogger()
    monkeypatch.setattr(wake_guard, "log", logger)
    monkeypatch.setattr(wake_guard, "WATCH_INTERVAL_SECONDS", 0.001)
    clock = FakeTime()

    task = asyncio.create_task(watch_sleep(clock.guard()))
    await asyncio.sleep(0.01)
    clock.wall += 3600
    await asyncio.sleep(0.01)
    task.cancel()

    assert len(logger.calls) == 1
