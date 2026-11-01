"""失败分类表和退避（spec 0005），都是纯函数，不依赖 discord 库。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

BACKOFF_BASE = 5.0
BACKOFF_MAX = 300.0

FailureKind = Literal[
    "captcha",
    "login_failure",
    "undecryptable",
    "required_action",
    "http",
    "rate_limited",
    "gateway_reconnect",
    "gateway_closed",
    "network",
]
VerdictStatus = Literal["reconnecting", "token_invalid", "verification_required"]


@dataclass(frozen=True)
class ConnectionFailure:
    kind: FailureKind
    http_status: int | None = None
    close_code: int | None = None
    retry_after: float | None = None
    during_login: bool = False


@dataclass(frozen=True)
class FailureVerdict:
    status: VerdictStatus
    last_error: str

    @property
    def stops_login(self) -> bool:
        return self.status != "reconnecting"


def classify_failure(failure: ConnectionFailure) -> FailureVerdict | None:
    """从上到下取第一条命中的行；None 表示意料之外，由调用方让进程崩溃。"""
    kind, status, code = failure.kind, failure.http_status, failure.close_code
    if kind == "captcha":
        return FailureVerdict("verification_required", "captcha_required")
    if kind == "login_failure" or status == 401:
        return FailureVerdict("token_invalid", "http_401")
    if kind == "gateway_closed" and code == 4004:
        return FailureVerdict("token_invalid", "gateway_close_4004")
    if kind == "undecryptable":
        return FailureVerdict("token_invalid", "token_undecryptable")
    if kind == "required_action":
        return FailureVerdict("verification_required", "required_action")
    if status == 403 and failure.during_login:
        return FailureVerdict("verification_required", "http_403")
    if kind == "rate_limited" or status == 429:
        return FailureVerdict("reconnecting", "http_429")
    if kind == "gateway_reconnect":
        return FailureVerdict("reconnecting", "gateway_reconnect")
    if status is not None and status >= 500:
        return FailureVerdict("reconnecting", "http_5xx")
    if kind == "network":
        return FailureVerdict("reconnecting", "network_error")
    if kind == "gateway_closed":
        return FailureVerdict("reconnecting", f"gateway_close_{code}")
    return None


def next_delay(attempt: int, uniform: Callable[[float, float], float]) -> float:
    """第 attempt 次重试（从 1 开始）前等待的秒数：5 秒起翻倍，封顶 5 分钟，带抖动。"""
    ceiling = min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (attempt - 1))
    return ceiling * uniform(0.8, 1.0)
