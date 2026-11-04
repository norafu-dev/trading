"""抓一页历史消息的结果（spec 0012 AC-12）：判别联合，边界之外不出现库的异常类型。"""

from dataclasses import dataclass

from harvester.db.snapshots import MessageSnapshot


@dataclass(frozen=True)
class Fetched:
    """`messages` 只含能转成快照的消息（从新到旧）；`returned_ids` 和 `raw_count` 是过滤前的。

    `request_count` 是这一页实际调用 Discord 的次数，包括为了拿频道对象额外调用的 `fetch_channel`。
    """

    messages: tuple[MessageSnapshot, ...]
    returned_ids: tuple[int, ...]
    raw_count: int
    request_count: int


@dataclass(frozen=True)
class RateLimited:
    retry_after: float


@dataclass(frozen=True)
class Forbidden:
    """403：小号没有权限。"""


@dataclass(frozen=True)
class NotFound:
    """404：频道不存在了。"""


@dataclass(frozen=True)
class Unavailable:
    """5xx、网络错误：不算失败，退避后重发同一页。"""

    reason: str


@dataclass(frozen=True)
class AccountRisk:
    """401、Cloudflare ban、要求验证码：整体暂停，等你手动继续。"""

    reason: str


@dataclass(frozen=True)
class Unexpected:
    error_type: str


HistoryPage = Fetched | RateLimited | Forbidden | NotFound | Unavailable | AccountRisk | Unexpected
