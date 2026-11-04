"""唯一导入 discord 的文件（AC-18）。库的类型和内部接口都关在这里，对外只有带类型的事件。

覆盖 connect 用到了库的内部接口，升级 discord-py-self 时要对照上游并重跑测试。
"""
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, cast

import aiohttp
import discord
import structlog
from curl_cffi import CurlError
from discord.gateway import DiscordWebSocket, ReconnectWebSocket

from harvester.collector.events import (
    ChannelRemoved,
    Disconnected,
    FullSync,
    GuildChanged,
    GuildRemoved,
    IdentifyRequested,
    LoggedIn,
    MessageEdited,
    MessageReceived,
    MessagesDeleted,
    Ready,
    RequiredAction,
    Resumed,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
    ThreadAccessLost,
    ThreadArchived,
)
from harvester.collector.failures import ConnectionFailure
from harvester.collector.wake_guard import WakeGuard
from harvester.db.snapshots import (
    ACCESS_CHANNEL_TYPES,
    AccessSnapshot,
    AttachmentSnapshot,
    ChannelSnapshot,
    EmbedAuthor,
    EmbedData,
    EmbedField,
    ForwardedData,
    GuildSnapshot,
    GuildState,
    MessageSnapshot,
    UserKind,
    UserSnapshot,
    normalize_embeds,
)

log = structlog.get_logger("harvester.collector.discord_client")

# 分类 (4)、文字 (0)、语音 (2)、公告 (5)、舞台 (13)、论坛 (15)、媒体 (16) 加线程；其余类型忽略。
_SNAPSHOT_CHANNEL_TYPES = frozenset({0, 2, 4, 5, 13, 15, 16, 10, 11, 12})

# 同一个服务器的事件按固定窗口合并，从第一个事件起算（不用尾随 debounce，AC-2）。
GUILD_COALESCE_SECONDS = 2.0
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


def snapshot_guild(guild: discord.Guild, lost_threads: frozenset[int] = frozenset()) -> GuildState:
    """只读 discord.py-self 已缓存的数据转成不可变快照，不发任何请求（spec 0006 AC-1）。

    权限在本地算：同时有「查看频道」和「读取消息历史」才算读得了。线程的权限只看父频道，
    所以小号被移出的线程要由调用方通过 lost_threads 传进来，否则快照会把权限写回去。
    """
    snapshot = GuildSnapshot(
        id=guild.id, name=guild.name, icon_hash=guild.icon.key if guild.icon else None
    )
    # 归档的线程可能留在缓存里（THREAD_UPDATE 的兜底分支），不算活跃线程。
    members = (*guild.channels, *(t for t in guild.threads if not t.archived))
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
            c.id not in lost_threads
            and (perms := c.permissions_for(me)).view_channel
            and perms.read_message_history,
        )
        for c in members
        if c.type.value in ACCESS_CHANNEL_TYPES
    )
    return GuildState(snapshot, channels, access)


def _user_snapshot(user: discord.User | discord.Member, kind: UserKind) -> UserSnapshot:
    return UserSnapshot(
        id=user.id,
        kind=kind,
        username=user.name,
        global_name=user.global_name,
        avatar_hash=user.avatar.key if user.avatar else None,
    )


def _embed_data(embed: discord.Embed) -> EmbedData:
    """按 spec 0002 只留需要的字段；值为空的键由 normalize_embeds 去掉。"""
    data: dict[str, object] = {
        "type": embed.type,
        "title": embed.title,
        "description": embed.description,
        "url": embed.url,
        "color": embed.colour.value if embed.colour is not None else None,
        "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
        "footer_text": embed.footer.text,
        "image_url": embed.image.url,
        "thumbnail_url": embed.thumbnail.url,
    }
    if embed.author.name:
        author = EmbedAuthor(name=embed.author.name)
        data["author"] = {**author, "url": embed.author.url} if embed.author.url else author
    if embed.fields:
        data["fields"] = [
            EmbedField(name=f.name or "", value=f.value or "", inline=bool(f.inline))
            for f in embed.fields
        ]
    return cast(EmbedData, data)


def _attachment_snapshot(attachment: discord.Attachment) -> AttachmentSnapshot:
    return AttachmentSnapshot(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size,
        url=attachment.url,
        width=attachment.width,
        height=attachment.height,
    )


def _forwarded(message: discord.Message) -> ForwardedData | None:
    reference = message.reference
    if (
        reference is None
        or reference.type is not discord.MessageReferenceType.forward
        or not message.message_snapshots
        or reference.message_id is None
    ):
        return None
    forwarded = message.message_snapshots[0]
    return ForwardedData(
        message_id=reference.message_id,
        channel_id=reference.channel_id,
        guild_id=reference.guild_id,
        content=forwarded.content,
        embeds=normalize_embeds([_embed_data(e) for e in forwarded.embeds]) or [],
        attachment_names=[a.filename for a in forwarded.attachments],
    )


def _forwarded_attachments(message: discord.Message) -> tuple[AttachmentSnapshot, ...]:
    """转发消息里的附件，序号就是 media.slot；不是转发时为空（spec 0011）。"""
    if _forwarded(message) is None:
        return ()
    return tuple(_attachment_snapshot(a) for a in message.message_snapshots[0].attachments)


def snapshot_message(
    message: discord.Message, *, attachments_known: bool = True
) -> MessageSnapshot | None:
    """转成不可变快照。私信、群聊和未知服务器的消息没有 guild，返回 None（AC-2）。"""
    guild = message.guild
    if guild is None:
        return None
    author = message.author
    kind: UserKind = (
        "webhook" if message.webhook_id is not None else "bot" if author.bot else "user"
    )
    reference = message.reference
    is_reply = message.type.value == 19 and reference is not None
    return MessageSnapshot(
        id=message.id,
        channel_id=message.channel.id,
        guild_id=guild.id,
        author=_user_snapshot(author, kind),
        # Member 依次取昵称、global_name、username；webhook 就是这条消息上的作者名。
        author_display_name=author.display_name,
        type=message.type.value,
        content=message.content,
        embeds=tuple(_embed_data(e) for e in message.embeds),
        forwarded=_forwarded(message),
        reply_to_id=reference.message_id if is_reply and reference is not None else None,
        edited_at=message.edited_at,
        attachments=tuple(_attachment_snapshot(a) for a in message.attachments),
        mentions=tuple(
            _user_snapshot(user, "bot" if user.bot else "user") for user in message.mentions
        ),
        attachments_known=attachments_known,
        forwarded_attachments=_forwarded_attachments(message),
    )


FullSyncData = tuple[tuple[GuildState, ...], frozenset[int]]


def snapshot_guilds(client: discord.Client, lost_threads: frozenset[int]) -> FullSyncData:
    available = tuple(snapshot_guild(g, lost_threads) for g in client.guilds if not g.unavailable)
    unavailable = frozenset(g.id for g in client.guilds if g.unavailable)
    return available, unavailable


# 最后一个参数就是服务器（guild_update 是 old, new）。
_GUILD_EVENTS = frozenset({"guild_join", "guild_available", "guild_update"})
# 最后一个参数带 .guild 的事件：频道、角色、新线程。
_MESSAGE_EVENTS = frozenset(
    {"message", "raw_message_edit", "raw_message_delete", "raw_bulk_message_delete"}
)
_GUILD_OWNED_EVENTS = frozenset(
    {
        "guild_channel_create",
        "guild_channel_update",
        "guild_role_create",
        "guild_role_update",
        "guild_role_delete",
        "thread_create",
    }
)


class HarvesterClient(discord.Client):
    """每个会话新建一个，不复用；所有退出路径都要 close()。"""

    def __init__(
        self,
        generation: int,
        events: "asyncio.Queue[SupervisorEvent]",
        clock: Callable[[], datetime],
        ws_factory: WebSocketFactory = DiscordWebSocket.from_client,
        coalesce_seconds: float = GUILD_COALESCE_SECONDS,
        guard: WakeGuard | None = None,
    ) -> None:
        super().__init__(
            status=discord.Status.invisible,
            sync_presence=False,  # 不改账号设置。
            chunk_guilds_at_startup=False,
            guild_subscriptions=False,  # READY 之后由监督循环按需订阅（spec 0008 AC-9）。
            max_ratelimit_timeout=MAX_RATELIMIT_TIMEOUT,
        )
        self._generation = generation
        self._events = events
        self._clock = clock
        self._ws_factory = ws_factory
        self._sockets: list[_Closable] = []
        self._coalesce_seconds = coalesce_seconds
        self._pending_guilds: dict[int, asyncio.TimerHandle] = {}
        self._lost_threads: frozenset[int] = frozenset()
        self._resume_rejected = False
        self._guard = guard
        self._track_sockets()
        self._watch_thread_updates()
        self._watch_thread_list_sync()

    def _watch_thread_updates(self) -> None:
        """归档的线程不在缓存里，取消归档时库走兜底分支、不派发事件，只能在解析器这里接住。"""
        state = self._connection
        original = state.parsers["THREAD_UPDATE"]

        def parse(data: Any) -> None:
            guild = state._get_guild(int(data["guild_id"]))
            thread_id = int(data["id"])
            cached = guild is not None and guild.get_thread(thread_id) is not None
            original(data)
            archived = data.get("thread_metadata", {}).get("archived", False)
            if guild is not None and not cached and not archived:
                self._events.put_nowait(ThreadArchived(self._generation, thread_id, False))
                self._schedule_guild(guild.id)

        state.parsers["THREAD_UPDATE"] = parse

    def _watch_thread_list_sync(self) -> None:
        """订阅后的线程列表不派发通用事件，不接住的话，这些线程进不了快照（spec 0008）。"""
        state = self._connection
        original = state.parsers["THREAD_LIST_SYNC"]

        def parse(data: Any) -> None:
            original(data)
            self._schedule_guild(int(data["guild_id"]))

        state.parsers["THREAD_LIST_SYNC"] = parse

    async def subscribe_guilds(self, guild_ids: frozenset[int]) -> frozenset[int]:
        """返回实际发出的服务器；库会把多个服务器合并成一条网关指令发送。"""
        sent: set[int] = set()
        for guild_id in sorted(guild_ids):
            guild = self.get_guild(guild_id)
            if guild is None or guild.unavailable:
                continue
            try:
                await guild.subscribe(typing=True, threads=True)
            except Exception as exc:
                log.warning(
                    "discord_client.subscribe_failed",
                    guild_id=guild_id,
                    error_type=type(exc).__name__,
                )
                continue
            sent.add(guild_id)
        return frozenset(sent)

    def _route_message_event(self, event: str, args: tuple[Any, ...]) -> None:
        """转快照出错只跳过这一条，不能影响网关连接（AC-8）。"""
        generation = self._generation
        if event == "message":
            message = args[0]
            snapshot = snapshot_message(message)
            if snapshot is not None:
                self._events.put_nowait(MessageReceived(generation, snapshot, self._clock()))
        elif event == "raw_message_edit":
            raw = args[0]
            snapshot = snapshot_message(raw.message, attachments_known="attachments" in raw.data)
            if snapshot is not None:
                self._events.put_nowait(MessageEdited(generation, snapshot, self._clock()))
        elif event == "raw_message_delete":
            raw = args[0]
            self._events.put_nowait(
                MessagesDeleted(generation, raw.channel_id, (raw.message_id,), self._clock())
            )
        elif event == "raw_bulk_message_delete":
            raw = args[0]
            self._events.put_nowait(
                MessagesDeleted(
                    generation, raw.channel_id, tuple(sorted(raw.message_ids)), self._clock()
                )
            )

    def _schedule_guild(self, guild_id: int) -> None:
        if guild_id not in self._pending_guilds:
            self._pending_guilds[guild_id] = asyncio.get_running_loop().call_later(
                self._coalesce_seconds, self._flush_guild, guild_id
            )

    def _flush_guild(self, guild_id: int) -> None:
        del self._pending_guilds[guild_id]
        guild = self.get_guild(guild_id)
        # 已经不在缓存里就丢弃，否则排在 GuildRemoved 之后的写入会把服务器改回 active。
        if guild is None or guild.unavailable:
            return
        state = snapshot_guild(guild, self._lost_threads)
        self._events.put_nowait(GuildChanged(self._generation, state))

    def _route_channel_event(self, event: str, args: tuple[Any, ...]) -> None:
        """把库的事件转成监督循环的事件；这里不写库。"""
        generation = self._generation
        if event in _GUILD_EVENTS:
            self._schedule_guild(args[-1].id)
        elif event in _GUILD_OWNED_EVENTS:
            self._schedule_guild(args[-1].guild.id)
        elif event == "member_update":
            if self.user is not None and args[-1].id == self.user.id:
                self._schedule_guild(args[-1].guild.id)
        elif event == "thread_join":
            self._lost_threads -= {args[0].id}
            self._schedule_guild(args[0].guild.id)
        elif event == "thread_update":
            old, thread = args
            if old.archived != thread.archived:
                self._events.put_nowait(ThreadArchived(generation, thread.id, thread.archived))
            if not thread.archived:
                self._schedule_guild(thread.guild.id)
        elif event == "thread_remove":
            self._lost_threads |= {args[0].id}
            self._events.put_nowait(ThreadAccessLost(generation, args[0].id))
        elif event == "raw_thread_delete":
            self._events.put_nowait(ChannelRemoved(generation, args[0].thread_id))
        elif event == "guild_channel_delete":
            self._events.put_nowait(ChannelRemoved(generation, args[0].id))
        elif event == "guild_remove":
            pending = self._pending_guilds.pop(args[0].id, None)
            if pending is not None:
                pending.cancel()
            self._events.put_nowait(GuildRemoved(generation, args[0].id))

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
                self._lost_threads = frozenset()
                states, unavailable_ids = snapshot_guilds(self, self._lost_threads)
                self._events.put_nowait(FullSync(generation, states, unavailable_ids))
        elif event == "resumed":
            self._events.put_nowait(Resumed(generation))
        elif event == "disconnect":
            self._events.put_nowait(Disconnected(generation, self._clock()))
        elif event == "required_action_update" and args and args[0] is not None:
            self._events.put_nowait(RequiredAction(generation))
        elif event in _MESSAGE_EVENTS:
            try:
                self._route_message_event(event, args)
            except Exception as exc:
                log.warning("discord_client.message_snapshot_failed", error_type=type(exc).__name__)
        else:
            self._route_channel_event(event, args)
        super().dispatch(event, *args, **kwargs)

    def _new_reply(self) -> "asyncio.Future[Any]":
        return asyncio.get_running_loop().create_future()

    async def before_identify_hook(self, *, initial: bool = False) -> None:
        reply: asyncio.Future[bool] = self._new_reply()
        self._events.put_nowait(IdentifyRequested(self._generation, reply, self._resume_rejected))
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
            # 在打开 socket 之前等：hook 里等会撞上连接超时（spec 0007）。RESUME 不花额度，不用等。
            if self._guard is not None and not resuming:
                await self._guard.wait_until_settled()
            try:
                coro = self._ws_factory(self, **params)
                self.ws = await asyncio.wait_for(coro, timeout=GATEWAY_CONNECT_TIMEOUT)
                self._resume_rejected = False
                params = {"initial": False}
                while True:
                    await self.ws.poll_event()
            except ReconnectWebSocket as exc:
                if self.is_closed():
                    return
                self.dispatch("disconnect")
                params = self._next_params(resume=exc.resume)
                self._resume_rejected = resuming and not exc.resume
                proceed = await self._wait_before_retry(
                    reconnect_failure(self.ws),
                    immediate_allowed=params.get("resume", False),
                    resume_ready=params.get("resume", False),
                    resume_rejected=self._resume_rejected,
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
                self._resume_rejected = False
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
        for pending in self._pending_guilds.values():
            pending.cancel()
        self._pending_guilds = {}
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
    guard: WakeGuard | None = None,
) -> HarvesterClient:
    return HarvesterClient(generation, events, clock, guard=guard)
