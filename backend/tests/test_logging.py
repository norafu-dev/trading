"""日志文件（spec 0007）：路径、轮转、权限、脱敏和打不开时的失败。"""

import json
import logging
import stat
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest
import structlog

from harvester import logging as harvester_logging
from harvester.config import Settings
from harvester.logging import configure_logging

TOKEN = "fake-discord-token.abc123.XYZ"


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    yield
    structlog.reset_defaults()
    for handler in logging.getLogger().handlers:
        handler.close()
    logging.getLogger().handlers = []


def _events(text: str) -> list[dict[str, object]]:
    """去掉时间戳：标准库日志的时间戳是每个 handler 各算一次，会差几微秒。"""
    parsed = (json.loads(line) for line in text.splitlines())
    return [{k: v for k, v in e.items() if k != "timestamp"} for e in parsed]


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("process", ["collector", "api"])
def test_file_gets_the_same_json_lines_as_stdout(  # covers: AC-7
    tmp_path: Path, process: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO", process, tmp_path / "logs")

    structlog.get_logger("test").info("hello", channel_id=1)
    logging.getLogger("test.file_stdlib").warning("from stdlib")

    written = _events((tmp_path / "logs" / f"{process}.log").read_text())
    assert written == _events(capsys.readouterr().out)
    assert [e["event"] for e in written] == ["hello", "from stdlib"]


@pytest.mark.parametrize(
    ("process", "log_dir"), [(None, None), ("api", None), (None, Path("unused"))]
)
def test_no_file_unless_both_are_set(  # covers: AC-7
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: str | None,
    log_dir: Path | None,
) -> None:
    monkeypatch.chdir(tmp_path)

    configure_logging("INFO", process, log_dir)

    assert list(tmp_path.iterdir()) == []
    assert [type(h) for h in logging.getLogger().handlers] == [logging.StreamHandler]


def test_rotation_keeps_a_bounded_number_of_files(  # covers: AC-8
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert harvester_logging.LOG_MAX_BYTES == 10 * 1024 * 1024
    assert harvester_logging.LOG_BACKUP_COUNT == 5
    monkeypatch.setattr(harvester_logging, "LOG_MAX_BYTES", 200)

    configure_logging("INFO", "collector", tmp_path)
    for index in range(100):
        structlog.get_logger("test").info("line", index=index)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["collector.log", *(f"collector.log.{n}" for n in range(1, 6))]


def test_file_masks_secrets_like_stdout(tmp_path: Path) -> None:  # covers: AC-9
    configure_logging("INFO", "collector", tmp_path)

    structlog.get_logger("test").info("x", token=TOKEN, password=TOKEN, authorization=TOKEN)
    logging.getLogger("stdlib").info("y", extra={"token": TOKEN})

    assert TOKEN not in (tmp_path / "collector.log").read_text()


def test_directory_is_private_even_if_it_already_exists(tmp_path: Path) -> None:  # covers: AC-9
    created, existing = tmp_path / "a" / "new", tmp_path / "wide"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)

    configure_logging("INFO", "api", created)
    configure_logging("INFO", "api", existing)

    assert _mode(created) == 0o700
    assert _mode(existing) == 0o700


def test_logs_directory_is_ignored_by_git() -> None:  # covers: AC-9
    root = Path(__file__).resolve().parents[2]
    assert "logs/" in (root / ".gitignore").read_text().splitlines()


def test_unusable_directory_raises(tmp_path: Path) -> None:  # covers: AC-10
    blocker = tmp_path / "file"
    blocker.write_text("")

    with pytest.raises(OSError):
        configure_logging("INFO", "api", blocker / "logs")


def test_unopenable_file_raises(tmp_path: Path) -> None:  # covers: AC-10
    (tmp_path / "api.log").mkdir()

    with pytest.raises(OSError):
        configure_logging("INFO", "api", tmp_path)


def test_reconfiguring_closes_the_old_file(tmp_path: Path) -> None:  # covers: AC-7
    configure_logging("INFO", "api", tmp_path)
    [old] = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]

    configure_logging("INFO")

    assert old.stream is None or old.stream.closed


def test_empty_log_dir_means_unset(monkeypatch: pytest.MonkeyPatch) -> None:  # covers: AC-7
    monkeypatch.setenv("LOG_DIR", "")
    assert Settings(_env_file=None).log_dir is None  # pyright: ignore[reportCallIssue]

    monkeypatch.setenv("LOG_DIR", "logs")
    assert Settings(_env_file=None).log_dir == Path("logs")  # pyright: ignore[reportCallIssue]


def test_access_log_drops_the_query_string(  # covers: spec 0013 AC-25
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")

    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:1",
        "GET",
        "/api/search/messages?q=SECRETWORD&author=42",
        "1.1",
        422,
    )

    out = capsys.readouterr().out
    assert "SECRETWORD" not in out
    assert "/api/search/messages HTTP/1.1" in out


def test_access_log_keeps_only_errors(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    for status in (200, 304, 404, 500):
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/api/x", "1.1", status)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2
    assert '" 404' in lines[0] and '" 500' in lines[1]
