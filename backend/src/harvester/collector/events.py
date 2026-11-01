"""会话发给监督循环的事件。事件处理器不写库，所有写入都由监督循环按队列顺序执行。

带 reply 的事件要等监督循环的答复：写完库、算好等待时间之后，会话才继续往下走。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime

from harvester.collector.failures import ConnectionFailure
from harvester.db.snapshots import GuildState


@dataclass(frozen=True)
class Ready:
    generation: int
    required_action: bool


@dataclass(frozen=True)
class Resumed:
    generation: int


@dataclass(frozen=True)
class Disconnected:
    generation: int
    at: datetime


@dataclass(frozen=True)
class RequiredAction:
    generation: int


@dataclass(frozen=True)
class LoggedIn:
    generation: int
    user_id: int
    username: str
    reply: "asyncio.Future[bool]"  # False 表示不要继续连接


@dataclass(frozen=True)
class RetryNeeded:
    generation: int
    failure: ConnectionFailure
    # 服务端让重连且能 RESUME：上一次连接足够稳定时可以立即重连。
    immediate_allowed: bool
    reply: "asyncio.Future[float | None]"  # 要等的秒数；None 表示不再重试
    # 下一次连接会尝试 RESUME，不是重新 IDENTIFY。
    resume_ready: bool = False
    # 这次连接带 resume 发起，但服务端拒绝了：下一次要重新 IDENTIFY。
    resume_rejected: bool = False


@dataclass(frozen=True)
class IdentifyRequested:
    generation: int
    reply: "asyncio.Future[bool]"  # False 表示额度用完或配置已变，不能发 IDENTIFY


@dataclass(frozen=True)
class SessionEnded:
    generation: int
    failure: ConnectionFailure | None = None
    unexpected: Exception | None = None


@dataclass(frozen=True)
class FullSync:
    """READY 之后紧接着放入：当前缓存里所有服务器的状态（spec 0006）。"""

    generation: int
    states: tuple[GuildState, ...]
    # 缓存里有但暂时不可用的服务器：不能据此判断已经退出。
    unavailable_ids: frozenset[int]


@dataclass(frozen=True)
class GuildChanged:
    """一个服务器的内容变了，client 合并 2 秒后取的快照。"""

    generation: int
    state: GuildState


@dataclass(frozen=True)
class GuildRemoved:
    generation: int
    guild_id: int


@dataclass(frozen=True)
class ChannelRemoved:
    generation: int
    channel_id: int


@dataclass(frozen=True)
class ThreadArchived:
    """不依赖快照：归档的线程通常不在缓存里。"""

    generation: int
    thread_id: int
    archived: bool


@dataclass(frozen=True)
class ThreadAccessLost:
    generation: int
    thread_id: int


@dataclass(frozen=True)
class Wake:
    """账号可能变了（NOTIFY 或轮询）。"""


@dataclass(frozen=True)
class ChannelsWake:
    """频道的采集开关可能变了（NOTIFY 或轮询）：只做覆盖对齐。"""


@dataclass(frozen=True)
class HeartbeatDue:
    pass


SessionEvent = (
    Ready
    | Resumed
    | Disconnected
    | RequiredAction
    | LoggedIn
    | RetryNeeded
    | IdentifyRequested
    | SessionEnded
    | FullSync
    | GuildChanged
    | GuildRemoved
    | ChannelRemoved
    | ThreadArchived
    | ThreadAccessLost
)
SupervisorEvent = SessionEvent | Wake | ChannelsWake | HeartbeatDue
