"""429 计数（spec 0012 AC-7）：只从 `discord.http` 的限速警告日志里数。

discord.py-self 每收到一次 429 都先记一条 `We are being rate limited` 警告再自己等待重试，只有
等待时间超过 60 秒才抛 `RateLimited`，所以只靠异常数不全，也会重复。同一次 429 后面可能跟着的
`Global rate limit has been hit`、`Cloudflare rate limit has been hit` 不再重复计数。
实时会话和附件换链接发出的请求也算（它们同样会让小号被盯上）。
"""

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

RATE_LIMIT_LOGGER = "discord.http"
RATE_LIMIT_PREFIX = "We are being rate limited"
# 任意 60 分钟内达到 3 次就自动暂停。
RATE_LIMIT_WINDOW = timedelta(minutes=60)
RATE_LIMIT_THRESHOLD = 3


@dataclass
class RateLimitLog:
    """最近 429 的时间。显式传入的可变容器（和 `GatewayHandle` 同类），不是模块级状态。"""

    hits: deque[datetime] = field(default_factory=lambda: deque(maxlen=64))

    def record(self, at: datetime) -> None:
        self.hits.append(at)

    def count_since(self, since: datetime) -> int:
        return sum(1 for at in self.hits if at >= since)

    def clear(self) -> None:
        self.hits.clear()


def is_tripped(log: RateLimitLog, now: datetime) -> bool:
    return log.count_since(now - RATE_LIMIT_WINDOW) >= RATE_LIMIT_THRESHOLD


class _RateLimitHandler(logging.Handler):
    def __init__(self, log: RateLimitLog, clock: Callable[[], datetime]) -> None:
        super().__init__(level=logging.WARNING)
        self._log = log
        self._clock = clock

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith(RATE_LIMIT_PREFIX):
            self._log.record(self._clock())


def install_ratelimit_watch(log: RateLimitLog, clock: Callable[[], datetime]) -> logging.Handler:
    """给 `discord.http` 加一个只数 429 的 handler；返回它，测试里可以摘掉。"""
    handler = _RateLimitHandler(log, clock)
    logging.getLogger(RATE_LIMIT_LOGGER).addHandler(handler)
    return handler


def ratelimit_watch_healthy() -> bool:
    """这个 logger 的有效级别不高于 WARNING 并且没被禁用，钩子才收得到记录。"""
    logger = logging.getLogger(RATE_LIMIT_LOGGER)
    return not logger.disabled and logger.getEffectiveLevel() <= logging.WARNING
