"""下载和读取的纯规则（spec 0011）：退避、失败分类、准入、响应头。没有任何 IO。"""

from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import quote

from harvester.media.urls import link_expires_at

# 可以重试的失败：第 1 到 4 次按这个间隔退避，第 5 次记为 failed（AC-10）。
RETRY_BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
MAX_ATTEMPTS = 5
# 链接距过期不到这么久就先换链接（AC-11）。
REFRESH_MARGIN = timedelta(minutes=5)
# 换过链接后这么久之内算「刚换过」。
RECENT_REFRESH = timedelta(minutes=10)

# 签名链接有效期和浏览器缓存（AC-14）：缓存比链接短，缓存里的链接不会已经过期。
PRESIGN_SECONDS = 3600
BROWSER_CACHE_SECONDS = 3000

INLINE_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
        "image/bmp",
        "image/avif",
        "video/mp4",
        "video/webm",
    }
)

FailureAction = Literal["retry", "refresh", "gone"]
Admission = Literal["ok", "too_large", "no_space"]


def next_backoff(attempts: int) -> timedelta:
    """`attempts` 是这次失败之后的累计次数（1 到 4）。"""
    return RETRY_BACKOFF[min(max(attempts, 1), len(RETRY_BACKOFF)) - 1]


def is_recently_refreshed(refreshed_at: datetime | None, now: datetime) -> bool:
    return refreshed_at is not None and now - refreshed_at <= RECENT_REFRESH


def link_needs_refresh(url: str, now: datetime) -> bool:
    """链接已过期或不到 5 分钟就过期；没有 `ex` 视为没过期。"""
    expires = link_expires_at(url)
    return expires is not None and expires - now < REFRESH_MARGIN


def classify_download_error(status: int | None, *, recently_refreshed: bool) -> FailureAction:
    """`status` 为空表示没有响应（超时、连接错误、R2 写入失败）。"""
    if status is None or status == 429 or status >= 500:
        return "retry"
    if status == 403:
        return "retry" if recently_refreshed else "refresh"
    if status == 404:
        return "gone" if recently_refreshed else "refresh"
    return "gone"  # 3xx 和其他 4xx


def admit(declared_size: int | None, max_file: int, used: int, quota: int) -> Admission:
    """下载前的准入：声明的大小超过上限，或总容量已经满了（AC-6、AC-7）。"""
    if declared_size is not None and declared_size > max_file:
        return "too_large"
    if used >= quota:
        return "no_space"
    return "ok"


def is_inline_type(content_type: str) -> bool:
    return content_type in INLINE_TYPES or content_type.startswith("audio/")


def _clean_filename(filename: str) -> str:
    return "".join(c for c in filename if c.isprintable() and c not in '"\\')


def content_disposition(content_type: str, filename: str, *, download: bool) -> str:
    """白名单类型内联打开，其他一律下载；文件名清理掉控制字符、引号和反斜杠。"""
    if not download and is_inline_type(content_type):
        return "inline"
    cleaned = _clean_filename(filename)
    ascii_name = "".join(c if c.isascii() else "_" for c in cleaned).strip() or "file"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(cleaned or 'file')}"
