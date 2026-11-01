"""唯一导入 discord 的文件（AC-18）。库的类型和内部接口都关在这里，对外只有带类型的事件。

覆盖 connect 用到了库的内部接口，升级 discord-py-self 时要对照上游并重跑测试。
"""
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

import aiohttp
import discord
import structlog
from curl_cffi import CurlError
from discord.gateway import DiscordWebSocket, ReconnectWebSocket

from harvester.collector.events import (
    Disconnected,
    FullSync,
    IdentifyRequested,
    LoggedIn,
    Ready,
    RequiredAction,
    Resumed,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
)
from harvester.collector.failures import ConnectionFailure
from harvester.db.snapshots import (
    ACCESS_CHANNEL_TYPES,
    AccessSnapshot,
    ChannelSnapshot,
    GuildSnapshot,
    GuildState,
)

log = structlog.get_logger("harvester.collector.discord_client")

# 分类 (4)、文字 (0)、语音 (2)、公告 (5)、舞台 (13)、论坛 (15)、媒体 (16) 加线程；其余类型忽略。
_SNAPSHOT_CHANNEL_TYPES = frozenset({0, 2, 4, 5, 13, 15, 16, 10, 11, 12})

GATEWAY_CONNECT_TIMEOUT = 60.0
MAX_RATELIMIT_TIMEOUT = 60.0

# from_client 的类型：测试用假的替换它，不连真的 Discord。
WebSocketFactory = Callable[..., Awaitable[Any]]


class _IdentifyDenied(Exception):
    """额度用完或配置已变。这时新 socket 已连上但还没赋给 self.ws，由 close() 释放。"""


class _Closable(Protocol):
    async def close(self) -> Any: ...


def _retry_after_header(exc: discord.HTTPException) -> float | None:
    raw = exc.response.headers.get("Retry-After")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def to_failure(exc: Exception, *, during_login: bool = False) -> ConnectionFailure | None:
    """把库的异常转成 ConnectionFailure；None 表示这不是可预期的连接失败。"""
    # CaptchaRequired 是 HTTPException 的子类，必须先判断。
    if isinstance(exc, discord.CaptchaRequired):
        return ConnectionFailure("captcha", http_status=exc.status, during_login=during_login)
    if isinstance(exc, discord.LoginFailure):
        return ConnectionFailure("login_failure", http_status=401, during_login=during_login)
    if isinstance(exc, discord.RateLimited):
        return ConnectionFailure(
            "rate_limited", retry_after=exc.retry_after, during_login=during_login
        )
    if isinstance(exc, discord.HTTPException):
        return ConnectionFailure(
            "http",
            http_status=exc.status,
            retry_after=_retry_after_header(exc),
            during_login=during_login,
        )
    if isinstance(exc, discord.ConnectionClosed):
        return ConnectionFailure("gateway_closed", close_code=exc.code)
    if isinstance(
        exc, OSError | aiohttp.ClientError | CurlError | TimeoutError | discord.GatewayNotFound
    ):
        return ConnectionFailure("network", during_login=during_login)
    return None


def reconnect_failure(ws: Any) -> ConnectionFailure:
    """ReconnectWebSocket 不带 code，按这个顺序取：本端按指令关闭、服务端的 close code、断网。"""
    if ws is None:
        return ConnectionFailure("network")
    if ws._close_code is not None:
        return ConnectionFailure("gateway_reconnect")
    code = ws.socket.close_code
    if code:
        return ConnectionFailure("gateway_closed", close_code=code)
    return ConnectionFailure("network")


def _channel_snapshot(
    guild_id: int, channel: discord.abc.GuildChannel | discord.Thread
) -> ChannelSnapshot:
    if isinstance(channel, discord.Thread):
        parent_id: int | None = channel.parent_id
        position = None
    else:
        parent_id = channel.category_id
        position = channel.position
    return ChannelSnapshot(
        id=channel.id,
        guild_id=guild_id,
        parent_id=parent_id,
        type=channel.type.value,
        name=channel.name,
        position=position,
    )


def snapshot_guild(guild: discord.Guild) -> GuildState:
    """只读 discord.py-self 已缓存的数据转成不可变快照，不发任何请求（spec 0006 AC-1）。

    权限在本地算：同时有「查看频道」和「读取消息历史」才算读得了。
    """
    snapshot = GuildSnapshot(
        id=guild.id, name=guild.name, icon_hash=guild.icon.key if guild.icon else None
    )
    members = (*guild.channels, *guild.threads)
    channels = tuple(
        _channel_snapshot(guild.id, c) for c in members if c.type.value in _SNAPSHOT_CHANNEL_TYPES
    )
    me = guild.me
    if me is None:  # pyright: ignore[reportUnnecessaryComparison]
        log.warning("discord_client.me_unknown", guild_id=guild.id)
        return GuildState(snapshot, channels, (), me_known=False)
    access = tuple(
        AccessSnapshot(
            c.id,
            (perms := c.permissions_for(me)).view_channel and perms.read_message_history,
        )
        for c in members
        if c.type.value in ACCESS_CHANNEL_TYPES
    )
    return GuildState(snapshot, channels, access)


FullSyncData = tuple[tuple[GuildState, ...], frozenset[int]]


def snapshot_guilds(client: discord.Client) -> FullSyncData:
    available = tuple(snapshot_guild(g) for g in client.guilds if not g.unavailable)
    unavailable = frozenset(g.id for g in client.guilds if g.unavailable)
    return available, unavailable


class HarvesterClient(discord.Client):
    """每个会话新建一个，不复用；所有退出路径都要 close()。"""

    def __init__(
        self,
        generation: int,
        events: "asyncio.Queue[SupervisorEvent]",
        clock: Callable[[], datetime],
        ws_factory: WebSocketFactory = DiscordWebSocket.from_client,
    ) -> None:
        super().__init__(
            status=discord.Status.invisible,
            sync_presence=False,  # 不改账号设置。
            chunk_guilds_at_startup=False,
            max_ratelimit_timeout=MAX_RATELIMIT_TIMEOUT,
        )
        self._generation = generation
        self._events = events
        self._clock = clock
        self._ws_factory = ws_factory
        self._sockets: list[_Closable] = []
        self._track_sockets()

    def _track_sockets(self) -> None:
        """记下每个打开的 socket：预留额度失败时它还没赋给 self.ws，只能从这里关。"""
        connect: Callable[..., Awaitable[Any]] = self.http.ws_connect  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

        async def tracked(url: str, **kwargs: Any) -> Any:
            socket = await connect(url, **kwargs)
            self._sockets = [socket]
            return socket

        self.http.ws_connect = tracked

    def dispatch(self, event: str, /, *args: Any, **kwargs: Any) -> None:
        # 不用 on_xxx 处理器：那些是异步调度的。这里同步入队，顺序和库派发的顺序完全一致。
        generation = self._generation
        if event == "ready":
            required = self.required_action is not None
            self._events.put_nowait(Ready(generation, required_action=required))
            if not required:
                # 紧跟在 Ready 后放入：旧会话的 FullSync 会被下一个会话的代号过滤掉。
                states, unavailable_ids = snapshot_guilds(self)
                self._events.put_nowait(FullSync(generation, states, unavailable_ids))
        elif event == "resumed":
            self._events.put_nowait(Resumed(generation))
        elif event == "disconnect":
            self._events.put_nowait(Disconnected(generation, self._clock()))
        elif event == "required_action_update" and args and args[0] is not None:
            self._events.put_nowait(RequiredAction(generation))
        super().dispatch(event, *args, **kwargs)

    def _new_reply(self) -> "asyncio.Future[Any]":
        return asyncio.get_running_loop().create_future()

    async def before_identify_hook(self, *, initial: bool = False) -> None:
        reply: asyncio.Future[bool] = self._new_reply()
        self._events.put_nowait(IdentifyRequested(self._generation, reply))
        if not await reply:
            raise _IdentifyDenied

    async def _wait_before_retry(
        self,
        failure: ConnectionFailure,
        *,
        immediate_allowed: bool = False,
        resume_ready: bool = False,
        resume_rejected: bool = False,
    ) -> bool:
        """问监督循环要等多久并等待；返回 False 表示不要再试。"""
        reply: asyncio.Future[float | None] = self._new_reply()
        self._events.put_nowait(
            RetryNeeded(
                self._generation, failure, immediate_allowed, reply, resume_ready, resume_rejected
            )
        )
        delay = await reply
        if delay is None:
            return False
        if delay > 0:
            await asyncio.sleep(delay)
        return True

    async def connect(self, *, reconnect: bool = True) -> None:
        """保留库的流程，所有等待都走监督循环给的退避，RESUME 判定也更严格。"""
        params: dict[str, Any] = {"initial": True}
        while not self.is_closed():
            # 连上之后 params 会被改写，所以先记下这次是不是带 resume 发起的。
            resuming = params.get("resume", False)
            try:
                coro = self._ws_factory(self, **params)
                self.ws = await asyncio.wait_for(coro, timeout=GATEWAY_CONNECT_TIMEOUT)
                params = {"initial": False}
                while True:
                    await self.ws.poll_event()
            except ReconnectWebSocket as exc:
                if self.is_closed():
                    return
                self.dispatch("disconnect")
                params = self._next_params(resume=exc.resume)
                proceed = await self._wait_before_retry(
                    reconnect_failure(self.ws),
                    immediate_allowed=params.get("resume", False),
                    resume_ready=params.get("resume", False),
                    resume_rejected=resuming and not exc.resume,
                )
            except discord.ConnectionClosed:
                # 不可恢复的 close code（包括 4004）：结束这个会话，由监督循环决定下一步。
                if self.is_closed():
                    return
                self.dispatch("disconnect")
                raise
            except (
                OSError,
                discord.HTTPException,
                discord.GatewayNotFound,
                aiohttp.ClientError,
                TimeoutError,
                CurlError,
            ) as exc:
                if self.is_closed():
                    return
                self.dispatch("disconnect")
                failure = to_failure(exc)
                assert failure is not None
                params = self._next_params(resume=True)
                proceed = await self._wait_before_retry(
                    failure, resume_ready=params.get("resume", False)
                )
            if not proceed:
                return

    def _next_params(self, *, resume: bool) -> dict[str, Any]:
        """只有 ws 存在且 session_id、sequence 都有值才 RESUME，否则 IDENTIFY。"""
        ws = self.ws
        # 这个 ws 属性在库里标注成非空，实际上第一次连上之前是 None。
        if ws is None:  # pyright: ignore[reportUnnecessaryComparison]
            return {"initial": True}
        if resume and ws.session_id is not None and ws.sequence is not None:
            return {
                "initial": False,
                "resume": True,
                "session": ws.session_id,
                "sequence": ws.sequence,
                "gateway": ws.gateway,
            }
        return {"initial": False}

    async def _login_until_done(self, token: str) -> bool:
        """login 只是一次 REST 请求；网络失败在同一个 client 上退避重试。"""
        while True:
            try:
                await self.login(token)
                return True
            except Exception as exc:
                failure = to_failure(exc, during_login=True)
                if failure is None:
                    raise
            if not await self._wait_before_retry(failure):
                return False

    async def run_session(self, token: str) -> None:
        """login → 比对身份 → connect。结果都变成事件，异常不会传出去。"""
        ended = SessionEnded(self._generation)
        try:
            if await self._login_until_done(token):
                user = self.user
                assert user is not None
                reply: asyncio.Future[bool] = self._new_reply()
                self._events.put_nowait(LoggedIn(self._generation, user.id, user.name, reply))
                if await reply:
                    await self.connect()
        except _IdentifyDenied:
            pass
        except Exception as exc:
            failure = to_failure(exc)
            ended = (
                SessionEnded(self._generation, failure=failure)
                if failure is not None
                else SessionEnded(self._generation, unexpected=exc)
            )
        self._events.put_nowait(ended)

    async def close(self) -> None:
        await super().close()
        for socket in self._sockets:
            try:
                await socket.close()
            except Exception:
                continue
        self._sockets = []


def new_client(
    generation: int,
    events: "asyncio.Queue[SupervisorEvent]",
    clock: Callable[[], datetime],
) -> HarvesterClient:
    return HarvesterClient(generation, events, clock)
