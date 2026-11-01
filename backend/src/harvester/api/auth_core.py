"""认证里的纯函数：不碰数据库，时间和随机数都从参数传入（spec 0004）。"""

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_IDLE_LIMIT = timedelta(days=7)
SESSION_ABSOLUTE_LIMIT = timedelta(days=30)
SESSION_TOUCH_INTERVAL = timedelta(seconds=60)
SESSION_COOKIE_MAX_AGE = 2592000
SETUP_CODE_LIFETIME = timedelta(hours=1)
LOGIN_EVENT_RETENTION = timedelta(days=180)

TOTP_STEP_SECONDS = 30
USER_AGENT_MAX_CHARS = 512
UNKNOWN_IP = "0.0.0.0"

FAILURE_WINDOW = timedelta(hours=24)
BACKOFF_FREE_FAILURES = 5
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
GLOBAL_WINDOW = timedelta(seconds=60)
GLOBAL_MAX_FAILURES = 30
RATE_LIMITED_LOG_INTERVAL = timedelta(seconds=60)

_PASSWORD_HASHER = PasswordHasher()


@dataclass(frozen=True)
class RequestContext:
    ip: str
    user_agent: str | None


def request_context(client_host: str | None, user_agent: str | None) -> RequestContext:
    return RequestContext(
        ip=_valid_ip_or_unknown(client_host),
        user_agent=user_agent[:USER_AGENT_MAX_CHARS] if user_agent is not None else None,
    )


def _valid_ip_or_unknown(host: str | None) -> str:
    # 只有本地绕过 Caddy 直连时才可能不是合法 IP；inet 字段不能因此报错。
    if host is None:
        return UNKNOWN_IP
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return UNKNOWN_IP
    return host


def hash_token(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def new_session_token(random_token: Callable[[int], str] = secrets.token_urlsafe) -> str:
    return random_token(32)


def new_setup_code(random_token: Callable[[int], str] = secrets.token_urlsafe) -> str:
    return random_token(16)


def session_is_valid(created_at: datetime, last_seen_at: datetime, now: datetime) -> bool:
    return last_seen_at > now - SESSION_IDLE_LIMIT and created_at > now - SESSION_ABSOLUTE_LIMIT


def session_needs_touch(last_seen_at: datetime, now: datetime) -> bool:
    return now - last_seen_at >= SESSION_TOUCH_INTERVAL


def backoff_seconds(failures: int) -> int:
    if failures < BACKOFF_FREE_FAILURES:
        return 0
    # 指数很大时不算 2 ** n，结果反正封顶。
    exponent = min(failures - BACKOFF_FREE_FAILURES, 12)
    return min(BACKOFF_BASE_SECONDS * 2**exponent, BACKOFF_MAX_SECONDS)


def remaining_wait_seconds(failures: int, last_failure: datetime | None, now: datetime) -> int:
    """还要等多少秒，0 表示不用等。向上取整，避免提示的时间到了还被拒绝。"""
    wait = backoff_seconds(failures)
    if wait == 0 or last_failure is None:
        return 0
    remaining = (last_failure + timedelta(seconds=wait) - now).total_seconds()
    return max(ceil(remaining), 0)


def totp_step(now: datetime) -> int:
    return int(now.timestamp()) // TOTP_STEP_SECONDS


def verify_totp_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """依次试当前步、前一步、后一步，取第一个匹配的；它必须大于 last_step，否则算重复使用。"""
    current = totp_step(now)
    totp = pyotp.TOTP(secret)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.generate_otp(step).encode(), code.encode()):
            return step if last_step is None or step > last_step else None
    return None


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str) -> str:
    # pyotp 的 **kwargs 没标类型，只在这一处忽略。
    return pyotp.TOTP(secret).provisioning_uri(  # pyright: ignore[reportUnknownMemberType]
        name="dashboard", issuer_name="Harvester"
    )


def hash_password(password: str) -> str:
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _PASSWORD_HASHER.check_needs_rehash(password_hash)


def session_cookie_name(secure: bool) -> str:
    return "__Host-harvester_session" if secure else "harvester_session"
