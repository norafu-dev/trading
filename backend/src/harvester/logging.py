import logging
import re
import sys
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

# Any event field whose key matches is masked before it is rendered.
_SENSITIVE_KEY = re.compile(
    r"token|password|secret|authorization|cookie|api_key|dsn", re.IGNORECASE
)
_REDACTED = "***"

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def _redact(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in event_dict:
        if _SENSITIVE_KEY.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def strip_query(path: str) -> str:
    """去掉 URL 里 ? 之后的部分：搜索关键词在查询串里，不能进日志（spec 0013 AC-25）。"""
    return path.split("?", 1)[0]


def _strip_access_query(record: logging.LogRecord) -> bool:
    """uvicorn.access 的 args 是 (客户端, 方法, 带查询串的路径, 协议版本, 状态码)。"""
    args = record.args
    if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
        record.args = (args[0], args[1], strip_query(args[2]), args[3], args[4])
    return True


def _file_handler(process: str, log_dir: Path) -> RotatingFileHandler:
    """打不开就抛异常：配了 LOG_DIR 却写不了时要退出，不能悄悄退回只写 stdout（AC-10）。"""
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir 的 mode 受 umask 影响，也不会改已存在的目录，所以显式设一次。
    log_dir.chmod(0o700)
    return RotatingFileHandler(
        log_dir / f"{process}.log",
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=False,
    )


def configure_logging(
    level: str = "INFO", process: str | None = None, log_dir: Path | None = None
) -> None:
    """JSON lines to stdout for every process; stdlib loggers go through the same pipeline.

    process 和 log_dir 都有值时，同样的内容再写一份到 <log_dir>/<process>.log。
    """
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if process is not None and log_dir is not None:
        handlers.append(_file_handler(process, log_dir))
    for handler in handlers:
        handler.setFormatter(formatter)
    root = logging.getLogger()
    # 先关旧的，重复调用不会泄漏文件句柄。
    for old in root.handlers:
        old.close()
    root.handlers = handlers
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
    logging.getLogger("uvicorn.access").addFilter(_strip_access_query)
    # 固定在 WARNING，不跟随 LOG_LEVEL：discord 库的低级别日志带请求细节（spec 0005）。
    logging.getLogger("discord").setLevel(logging.WARNING)
