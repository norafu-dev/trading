"""换附件链接的结果类型（spec 0011 AC-11）：判别联合，不用异常表达可预期的失败。"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Refreshed:
    """Discord 返回 2xx 且能解析。`urls` 是 去掉查询参数的原链接 到 新链接；没出现的链接没被换。"""

    urls: Mapping[str, str]


@dataclass(frozen=True)
class RateLimited:
    retry_after: float


@dataclass(frozen=True)
class Unavailable:
    """5xx、网络错误、401、403、整批 400、响应解析不了：这一批的行都不动。"""

    reason: str


RefreshResult = Refreshed | RateLimited | Unavailable
