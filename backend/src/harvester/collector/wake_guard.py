"""睡眠保护（spec 0007）：电脑睡过之后，要连续醒满一段时间才发全新登录。

time.time() 在睡眠时继续走，time.monotonic() 不走，两者的差值就是睡了多久。
差值只会让登录变晚，不会放松额度检查。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

log = structlog.get_logger("harvester.collector.wake_guard")

SLEEP_GAP_SECONDS = 10.0
SETTLE_SECONDS = 90.0
WATCH_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class WakeState:
    wall: float | None = None
    mono: float | None = None
    # 最近一次发现睡眠时的单调时钟。
    slept_mono: float | None = None
    # 这次采样发现的差值；这次没发现睡眠就是 None。
    gap_seconds: float | None = None


def observe(state: WakeState, wall: float, mono: float) -> WakeState:
    """第一次采样只建立基准。时间向后跳时差值为负，不算睡眠。"""
    if state.wall is None or state.mono is None:
        return WakeState(wall, mono)
    gap = (wall - state.wall) - (mono - state.mono)
    if gap > SLEEP_GAP_SECONDS:
        return WakeState(wall, mono, slept_mono=mono, gap_seconds=gap)
    return WakeState(wall, mono, slept_mono=state.slept_mono)


def seconds_until_settled(state: WakeState, mono: float) -> float:
    if state.slept_mono is None:
        return 0.0
    return max(0.0, SETTLE_SECONDS - (mono - state.slept_mono))


class WakeGuard:
    """可变状态只在这里，由 run 创建一个并传下去。"""

    def __init__(
        self,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._wall = wall
        self._mono = mono
        self._sleep = sleep
        self._state = WakeState()

    def sample(self) -> None:
        self._state = observe(self._state, self._wall(), self._mono())
        if self._state.gap_seconds is not None:
            log.info("collector.sleep_detected", gap_seconds=round(self._state.gap_seconds, 1))

    async def wait_until_settled(self) -> None:
        """每轮先采样，刚醒时不会读到旧状态。靠任务取消退出，不检查连接状态。"""
        while True:
            self.sample()
            remaining = seconds_until_settled(self._state, self._mono())
            if remaining <= 0:
                return
            await self._sleep(min(remaining, WATCH_INTERVAL_SECONDS))


async def watch_sleep(guard: WakeGuard) -> None:
    """只为及时记录日志；是否放行由 wait_until_settled 重新采样决定。"""
    while True:
        guard.sample()
        await asyncio.sleep(WATCH_INTERVAL_SECONDS)
