"""spec 0012 AC-7：429 只从 `discord.http` 的限速警告日志里数。"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from harvester.collector.ratelimit_watch import (
    RateLimitLog,
    install_ratelimit_watch,
    is_tripped,
    ratelimit_watch_healthy,
)

NOW = datetime(2026, 3, 1, 12, tzinfo=UTC)
# 当前版本 discord.py-self 收到 429 时记的警告（discord/http.py）。
LIBRARY_WARNING = "We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds."


@pytest.fixture
def counted() -> Iterator[RateLimitLog]:
    log = RateLimitLog()
    handler = install_ratelimit_watch(log, lambda: NOW)
    logger = logging.getLogger("discord.http")
    old_level = logger.level
    logger.setLevel(logging.WARNING)
    try:
        yield log
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def test_counts_the_library_warning(counted: RateLimitLog) -> None:
    logging.getLogger("discord.http").warning(LIBRARY_WARNING, "GET", "/channels/1", 1.5)
    assert counted.count_since(NOW - timedelta(minutes=1)) == 1


def test_ignores_follow_up_messages_of_the_same_429(counted: RateLimitLog) -> None:
    logger = logging.getLogger("discord.http")
    logger.warning(LIBRARY_WARNING, "GET", "/x", 1.0)
    logger.warning("Global rate limit has been hit. Retrying in %.2f seconds.", 1.0)
    logger.warning("Cloudflare rate limit has been hit. Retrying in %.2f seconds.", 1.0)
    logger.warning("something else")
    assert len(counted.hits) == 1


def test_trips_at_three_within_sixty_minutes() -> None:
    log = RateLimitLog()
    for minutes in (59, 30):
        log.record(NOW - timedelta(minutes=minutes))
    assert not is_tripped(log, NOW)
    log.record(NOW - timedelta(minutes=1))
    assert is_tripped(log, NOW)


def test_old_hits_do_not_count() -> None:
    log = RateLimitLog()
    for minutes in (120, 90, 61, 5):
        log.record(NOW - timedelta(minutes=minutes))
    assert not is_tripped(log, NOW)


def test_clear_forgets_everything() -> None:
    log = RateLimitLog()
    for _ in range(3):
        log.record(NOW)
    log.clear()
    assert not is_tripped(log, NOW)


def test_healthy_requires_warning_level_and_enabled_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger("discord.http")
    monkeypatch.setattr(logger, "level", logging.WARNING)
    monkeypatch.setattr(logger, "disabled", False)
    assert ratelimit_watch_healthy()
    monkeypatch.setattr(logger, "level", logging.ERROR)
    assert not ratelimit_watch_healthy()
    monkeypatch.setattr(logger, "level", logging.WARNING)
    monkeypatch.setattr(logger, "disabled", True)
    assert not ratelimit_watch_healthy()


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "ERROR"])
def test_real_logging_setup_keeps_the_watch_healthy(level: str) -> None:
    """项目自己的日志配置把 discord 固定在 WARNING，钩子始终收得到 429 警告。"""
    from harvester.logging import configure_logging

    configure_logging(level)
    assert ratelimit_watch_healthy()
