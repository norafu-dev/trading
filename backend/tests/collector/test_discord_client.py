"""HarvesterClient 的边界：RESUME 判定、失败转换、socket 释放。Discord 用假的 from_client。"""

import ast
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest
from discord.gateway import ReconnectWebSocket
from discord.state import ConnectionState

import harvester
from harvester.collector.discord_client import (
    HarvesterClient,
    fetch_history_page,
    new_client,
    reconnect_failure,
    snapshot_guild,
    snapshot_message,
    to_failure,
)
from harvester.collector.events import (
    ChannelRemoved,
    Disconnected,
    GuildChanged,
    GuildRemoved,
    IdentifyRequested,
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
from harvester.collector.history_page import (
    AccountRisk,
    Fetched,
    Forbidden,
    NotFound,
    Unexpected,
)
from harvester.collector.history_page import RateLimited as HistoryRateLimited
from harvester.collector.history_page import Unavailable as HistoryUnavailable
from harvester.collector.wake_guard import SETTLE_SECONDS, WakeGuard
from harvester.db.snapshots import AccessSnapshot


class FakeSocket:
    def __init__(self, close_code: int | None = None) -> None:
        self.close_code = close_code
        self.closed = 0

    async def close(self, *_args: object) -> None:
        self.closed += 1


class FakeWs:
    def __init__(
        self,
        outcome: Exception,
        session_id: str | None = "session",
        sequence: int | None = 7,
        close_code: int | None = None,
        socket_code: int | None = None,
    ) -> None:
        self.outcome = outcome
        self.session_id = session_id
        self.sequence = sequence
        self.gateway = "wss://resume"
        self._close_code = close_code
        self.socket = FakeSocket(socket_code)
        self.open = False

    async def poll_event(self) -> None:
        raise self.outcome


Script = list[Callable[[HarvesterClient], Awaitable[Any]]]


def scripted_factory(script: Script, calls: list[dict[str, Any]]) -> Any:
    async def factory(client: HarvesterClient, **params: Any) -> Any:
        calls.append(params)
        return await script[len(calls) - 1](client)

    return factory


def returns(ws: Any) -> Callable[[HarvesterClient], Awaitable[Any]]:
    async def step(_client: HarvesterClient) -> Any:
        return ws

    return step


def raises(exc: Exception) -> Callable[[HarvesterClient], Awaitable[Any]]:
    async def step(_client: HarvesterClient) -> Any:
        raise exc

    return step


def now() -> datetime:
    return datetime(2026, 11, 1, tzinfo=UTC)


async def answer_retries(
    queue: "asyncio.Queue[SupervisorEvent]", seen: list[SupervisorEvent], *replies: float | None
) -> None:
    remaining = list(replies)
    while True:
        event = await queue.get()
        seen.append(event)
        if isinstance(event, RetryNeeded):
            event.reply.set_result(remaining.pop(0))


async def run_connect(
    script: Script, *replies: float | None
) -> tuple[list[dict[str, Any]], list[SupervisorEvent], BaseException | None]:
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    calls: list[dict[str, Any]] = []
    seen: list[SupervisorEvent] = []
    client = HarvesterClient(1, queue, now, scripted_factory(script, calls))
    responder = asyncio.create_task(answer_retries(queue, seen, *replies))
    error: BaseException | None = None
    try:
        await asyncio.wait_for(client.connect(), timeout=2)
    except discord.ConnectionClosed as exc:
        error = exc
    await asyncio.sleep(0)
    responder.cancel()
    return calls, seen, error


async def test_first_connect_failure_does_not_crash_and_identifies_again() -> None:  # AC-5
    calls, seen, _ = await run_connect(
        [raises(OSError("down")), raises(OSError("down")), raises(OSError("down"))], 0, 0, None
    )

    assert [c.get("resume", False) for c in calls] == [False, False, False]
    retries = [e for e in seen if isinstance(e, RetryNeeded)]
    assert [r.failure.kind for r in retries] == ["network"] * 3
    assert not any(r.immediate_allowed for r in retries)
    assert not any(r.resume_ready for r in retries)


async def test_reconnect_resumes_with_session_and_gateway() -> None:  # covers: AC-6
    ws = FakeWs(ReconnectWebSocket(resume=True), close_code=4000)
    calls, seen, _ = await run_connect([returns(ws), raises(OSError("x"))], 0, None)

    assert calls[1] == {
        "initial": False,
        "resume": True,
        "session": "session",
        "sequence": 7,
        "gateway": "wss://resume",
    }
    first = next(e for e in seen if isinstance(e, RetryNeeded))
    assert first.immediate_allowed
    assert first.resume_ready
    assert first.failure == ConnectionFailure("gateway_reconnect")
    assert isinstance(seen[0], Disconnected)


@pytest.mark.parametrize(
    "ws",
    [
        FakeWs(ReconnectWebSocket(resume=False), close_code=1000),
        FakeWs(ReconnectWebSocket(resume=True), session_id=None),
        FakeWs(ReconnectWebSocket(resume=True), sequence=None),
    ],
)
async def test_identify_when_resume_is_impossible(ws: FakeWs) -> None:  # covers: AC-6
    calls, seen, _ = await run_connect([returns(ws), raises(OSError("x"))], 0, None)

    assert calls[1] == {"initial": False}
    first = next(e for e in seen if isinstance(e, RetryNeeded))
    assert not first.immediate_allowed
    assert not first.resume_ready


async def test_rejected_resume_is_flagged_once_then_identifies() -> None:  # covers: AC-5
    ws = FakeWs(ReconnectWebSocket(resume=True), close_code=1000)
    # 真实的库在抛出前会清掉 session_id 和 sequence。
    rejecting = FakeWs(ReconnectWebSocket(resume=False), None, None, close_code=1000)
    fresh = FakeWs(ReconnectWebSocket(resume=False), None, None, close_code=1000)
    calls, seen, _ = await run_connect(
        [returns(ws), returns(rejecting), returns(fresh)], 0, 0, None
    )

    assert calls[1]["resume"]
    assert calls[2] == {"initial": False}
    retries = [e for e in seen if isinstance(e, RetryNeeded)]
    assert [r.resume_rejected for r in retries] == [False, True, False]
    assert not retries[1].resume_ready
    assert not retries[1].immediate_allowed


async def test_identify_request_says_when_resume_was_rejected() -> None:  # covers: AC-11
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    seen: list[IdentifyRequested] = []

    async def answer() -> None:
        while True:
            event = await queue.get()
            if isinstance(event, RetryNeeded):
                event.reply.set_result(None if len(seen) == 2 else 0)
            elif isinstance(event, IdentifyRequested):
                seen.append(event)
                event.reply.set_result(True)

    def identifies() -> Callable[[HarvesterClient], Awaitable[Any]]:
        async def step(client: HarvesterClient) -> Any:
            await client.before_identify_hook()
            return FakeWs(ReconnectWebSocket(resume=False), None, None, close_code=1000)

        return step

    ws = FakeWs(ReconnectWebSocket(resume=True), close_code=1000)
    rejecting = FakeWs(ReconnectWebSocket(resume=False), None, None, close_code=1000)
    calls: list[dict[str, Any]] = []
    script: Script = [returns(ws), returns(rejecting), identifies(), identifies()]
    client = HarvesterClient(1, queue, now, scripted_factory(script, calls))
    responder = asyncio.create_task(answer())

    await asyncio.wait_for(client.connect(), timeout=2)
    responder.cancel()

    # 只有紧接在被拒之后的那一次算，再下一次 IDENTIFY 不算。
    assert [e.resume_rejected for e in seen] == [True, False]


async def test_resume_failed_by_network_is_not_a_rejection() -> None:  # covers: AC-5
    ws = FakeWs(ReconnectWebSocket(resume=True), close_code=1000)
    _, seen, _ = await run_connect([returns(ws), raises(OSError("down"))], 0, None)

    assert not any(e.resume_rejected for e in seen if isinstance(e, RetryNeeded))


async def test_unrecoverable_close_propagates_after_disconnect() -> None:
    ws = FakeWs(discord.ConnectionClosed(4004))  # pyright: ignore[reportArgumentType]
    _, seen, error = await run_connect([returns(ws)])

    assert isinstance(error, discord.ConnectionClosed)
    assert isinstance(seen[0], Disconnected)


def test_reconnect_failure_code_order() -> None:
    signal = ReconnectWebSocket()
    assert reconnect_failure(
        FakeWs(signal, close_code=4000, socket_code=4001)
    ) == ConnectionFailure("gateway_reconnect")
    assert reconnect_failure(FakeWs(signal, socket_code=4009)) == ConnectionFailure(
        "gateway_closed", close_code=4009
    )
    assert reconnect_failure(FakeWs(signal)) == ConnectionFailure("network")
    assert reconnect_failure(None) == ConnectionFailure("network")


class FakeResponse:
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        self.status = status
        self.status_code = status
        self.reason = "x"
        self.headers = {"Retry-After": retry_after} if retry_after else {}


def http_error(status: int, retry_after: str | None = None) -> discord.HTTPException:
    return discord.HTTPException(FakeResponse(status, retry_after), "body")  # pyright: ignore[reportArgumentType]


def test_to_failure_maps_library_errors() -> None:  # covers: AC-8, AC-9
    assert to_failure(discord.LoginFailure(), during_login=True) == ConnectionFailure(
        "login_failure", http_status=401, during_login=True
    )
    assert to_failure(http_error(403), during_login=True) == ConnectionFailure(
        "http", http_status=403, during_login=True
    )
    assert to_failure(http_error(429, "12.5")) == ConnectionFailure(
        "http", http_status=429, retry_after=12.5
    )
    assert to_failure(OSError()) == ConnectionFailure("network")
    assert to_failure(TimeoutError()) == ConnectionFailure("network")
    assert to_failure(ValueError()) is None


async def test_login_retries_on_same_client_then_denied() -> None:  # covers: AC-5
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    client = HarvesterClient(1, queue, now)
    attempts: list[str] = []

    async def fail_login(token: str) -> None:
        attempts.append(token)
        raise http_error(502)

    client.login = fail_login  # pyright: ignore[reportAttributeAccessIssue]
    seen: list[SupervisorEvent] = []
    responder = asyncio.create_task(answer_retries(queue, seen, 0, None))

    await asyncio.wait_for(client.run_session("secret"), timeout=2)
    await asyncio.sleep(0)
    responder.cancel()
    await client.close()

    assert attempts == ["secret", "secret"]
    assert isinstance(seen[-1], SessionEnded) and seen[-1].failure is None


async def test_denied_identify_releases_unassigned_socket() -> None:  # covers: AC-7
    """预留额度失败时，新 socket 已连上但还没赋给 self.ws，close() 也要释放它。"""
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    socket = FakeSocket()

    async def factory(client: HarvesterClient, **_params: Any) -> Any:
        await client.http.ws_connect("wss://gateway")
        await client.before_identify_hook(initial=True)
        raise AssertionError("IDENTIFY 不该发出")

    client = HarvesterClient(1, queue, now, factory)

    async def fake_startup(_url: str, **_kwargs: Any) -> FakeSocket:
        return socket

    # 换掉内层连接函数，保留 client 外面那层记录 socket 的包装。
    closure = client.http.ws_connect.__closure__  # pyright: ignore
    assert closure is not None
    closure[0].cell_contents = fake_startup

    async def deny() -> None:
        event = await queue.get()
        assert isinstance(event, IdentifyRequested)
        event.reply.set_result(False)

    denier = asyncio.create_task(deny())
    client.login = _no_login  # pyright: ignore[reportAttributeAccessIssue]
    client._connection.user = _FakeUser()  # pyright: ignore

    async def allow_login() -> None:
        from harvester.collector.events import LoggedIn

        event = await queue.get()
        assert isinstance(event, LoggedIn)
        event.reply.set_result(True)
        await deny()

    denier.cancel()
    answering = asyncio.create_task(allow_login())
    await asyncio.wait_for(client.run_session("secret"), timeout=2)
    await answering
    assert isinstance(queue.get_nowait(), SessionEnded)

    await client.close()

    assert socket.closed == 1


async def _no_login(_token: str) -> None:
    return None


class _FakeUser:
    id = 1
    name = "fake"


def test_only_boundary_imports_discord() -> None:  # covers: AC-18
    root = Path(harvester.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(n == "discord" or n.startswith("discord.") for n in names):
                offenders.append(path.relative_to(root).as_posix())
    assert set(offenders) == {"collector/discord_client.py"}


def test_non_numeric_retry_after_is_ignored() -> None:
    assert to_failure(http_error(429, "soon")) == ConnectionFailure("http", http_status=429)


async def test_client_logs_in_invisible_without_touching_settings() -> None:  # covers: AC-2
    client = new_client(1, asyncio.Queue(), lambda: datetime(2026, 11, 1, tzinfo=UTC))
    state = client._connection  # pyright: ignore[reportPrivateUsage]

    assert state._status == "invisible"  # pyright: ignore[reportPrivateUsage]
    assert client._sync_presences is False  # pyright: ignore[reportPrivateUsage]
    assert state._chunk_guilds is False  # pyright: ignore[reportPrivateUsage]
    await client.close()


async def test_dispatch_queues_events_in_order_with_generation() -> None:  # covers: AC-13
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    moment = datetime(2026, 11, 1, tzinfo=UTC)
    client = HarvesterClient(7, queue, lambda: moment)

    client.dispatch("disconnect")
    client.dispatch("resumed")
    client.dispatch("ready")

    assert queue.get_nowait() == Disconnected(7, moment)
    assert queue.get_nowait() == Resumed(7)
    assert queue.get_nowait() == Ready(7, required_action=False)
    await client.close()


async def test_required_action_update_only_counts_when_set() -> None:  # covers: AC-9
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    client = HarvesterClient(7, queue, lambda: datetime(2026, 11, 1, tzinfo=UTC))

    client.dispatch("required_action_update", None)
    assert queue.empty()

    client.dispatch("required_action_update", "AGREEMENTS")
    assert queue.get_nowait() == RequiredAction(7)
    await client.close()


def _permissions(*, view: bool, history: bool) -> discord.Permissions:
    return discord.Permissions(view_channel=view, read_message_history=history)


def _fake_channel(
    cls: type, channel_id: int, type_value: int, perms: discord.Permissions | None = None
) -> MagicMock:
    channel = MagicMock(spec=cls)
    channel.id = channel_id
    channel.name = f"c{channel_id}"
    channel.type = SimpleNamespace(value=type_value)
    channel.category_id = None
    channel.position = 1
    channel.permissions_for.return_value = (
        _permissions(view=True, history=True) if perms is None else perms
    )
    return channel


def _fake_guild(channels: list[MagicMock], threads: list[MagicMock], me: object = "me") -> Any:
    return SimpleNamespace(id=9, name="g", icon=None, channels=channels, threads=threads, me=me)


@pytest.mark.parametrize(
    ("view", "history", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_can_read_needs_both_permissions(view: bool, history: bool, expected: bool) -> None:
    # covers: AC-3
    channel = _fake_channel(discord.TextChannel, 1, 0, _permissions(view=view, history=history))

    state = snapshot_guild(_fake_guild([channel], []))

    assert state.access == (AccessSnapshot(1, expected),)


def test_snapshot_writes_access_only_for_readable_types() -> None:  # covers: AC-1
    category = _fake_channel(discord.CategoryChannel, 1, 4)
    text = _fake_channel(discord.TextChannel, 2, 0)
    forum = _fake_channel(discord.ForumChannel, 3, 15)
    voice = _fake_channel(discord.VoiceChannel, 4, 2)
    unknown = _fake_channel(discord.TextChannel, 5, 99)
    thread = _fake_channel(discord.Thread, 6, 11)
    thread.parent_id = 3
    thread.archived = False

    state = snapshot_guild(_fake_guild([category, text, forum, voice, unknown], [thread]))

    assert [c.id for c in state.channels] == [1, 2, 3, 4, 6]
    assert {a.channel_id for a in state.access} == {2, 3, 6}
    snapshot = state.channels[-1]
    assert (snapshot.parent_id, snapshot.position) == (3, None)


def test_snapshot_without_me_has_no_access() -> None:  # covers: AC-3
    state = snapshot_guild(_fake_guild([_fake_channel(discord.TextChannel, 1, 0)], [], me=None))

    assert state.me_known is False
    assert state.access == ()
    assert len(state.channels) == 1


COALESCE = 0.02


def _coalescing_client(guild: Any) -> tuple[HarvesterClient, "asyncio.Queue[SupervisorEvent]"]:
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    client = HarvesterClient(
        1, queue, lambda: datetime(2026, 11, 1, tzinfo=UTC), coalesce_seconds=COALESCE
    )
    client.get_guild = lambda _id: guild  # type: ignore[method-assign]
    return client, queue


def _drain(queue: "asyncio.Queue[SupervisorEvent]") -> list[SupervisorEvent]:
    events: list[SupervisorEvent] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _guild_object() -> Any:
    guild = _fake_guild([_fake_channel(discord.TextChannel, 1, 0)], [])
    guild.unavailable = False
    return guild


async def test_events_of_one_guild_coalesce_into_one_write() -> None:  # covers: AC-2
    guild = _guild_object()
    client, queue = _coalescing_client(guild)
    channel = SimpleNamespace(guild=guild)

    client.dispatch("guild_channel_create", channel)
    client.dispatch("guild_channel_update", channel, channel)
    client.dispatch("guild_role_create", channel)
    client.dispatch("guild_update", guild, guild)
    assert queue.empty()
    await asyncio.sleep(COALESCE * 3)

    events = _drain(queue)
    assert len(events) == 1 and isinstance(events[0], GuildChanged)
    await client.close()


async def test_busy_guild_is_not_postponed_by_new_events() -> None:  # covers: AC-2
    guild = _guild_object()
    client, queue = _coalescing_client(guild)
    channel = SimpleNamespace(guild=guild)

    for _ in range(6):
        client.dispatch("guild_channel_update", channel, channel)
        await asyncio.sleep(COALESCE / 2)

    assert not queue.empty()
    await client.close()


async def test_guild_remove_cancels_the_pending_write() -> None:  # covers: AC-2
    guild = _guild_object()
    client, queue = _coalescing_client(guild)

    client.dispatch("guild_available", guild)
    client.dispatch("guild_remove", guild)
    await asyncio.sleep(COALESCE * 3)

    assert _drain(queue) == [GuildRemoved(1, guild.id)]
    await client.close()


async def test_guild_gone_at_flush_time_is_dropped() -> None:  # covers: AC-2
    client, queue = _coalescing_client(None)

    client.dispatch("guild_available", SimpleNamespace(id=9))
    await asyncio.sleep(COALESCE * 3)

    assert queue.empty()
    await client.close()


async def test_close_cancels_pending_writes() -> None:
    guild = _guild_object()
    client, queue = _coalescing_client(guild)

    client.dispatch("guild_available", guild)
    await client.close()
    await asyncio.sleep(COALESCE * 3)

    assert queue.empty()


async def test_delete_and_archive_events_are_routed() -> None:  # covers: AC-2, AC-8
    guild = _guild_object()
    client, queue = _coalescing_client(guild)

    client.dispatch("guild_channel_delete", SimpleNamespace(id=5))
    client.dispatch("raw_thread_delete", SimpleNamespace(thread_id=6))
    client.dispatch(
        "thread_update",
        SimpleNamespace(archived=False),
        SimpleNamespace(id=7, archived=True, guild=guild),
    )

    assert _drain(queue) == [
        ChannelRemoved(1, 5),
        ChannelRemoved(1, 6),
        ThreadArchived(1, 7, True),
    ]
    await client.close()


async def test_removed_thread_stays_unreadable_in_later_snapshots() -> None:  # covers: AC-8
    thread = _fake_channel(discord.Thread, 6, 12)
    thread.archived = False
    thread.parent_id = 1
    thread.guild = _fake_guild([], [thread])
    thread.guild.unavailable = False
    client, queue = _coalescing_client(thread.guild)

    client.dispatch("thread_remove", thread)
    client.dispatch("guild_available", thread.guild)
    await asyncio.sleep(COALESCE * 3)

    lost, changed = _drain(queue)
    assert lost == ThreadAccessLost(1, 6)
    assert isinstance(changed, GuildChanged)
    assert changed.state.access == (AccessSnapshot(6, False),)

    client.dispatch("thread_join", thread)
    await asyncio.sleep(COALESCE * 3)
    rejoined = _drain(queue)[0]
    assert isinstance(rejoined, GuildChanged)
    assert rejoined.state.access == (AccessSnapshot(6, True),)
    await client.close()


async def test_unarchive_of_uncached_thread_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    # covers: AC-2
    # 假 guild 走不通库自己的解析器，这里只验证包装层：原解析器换成记录调用。
    parsed: list[object] = []
    monkeypatch.setattr(ConnectionState, "parse_thread_update", lambda _self, d: parsed.append(d))
    guild = _guild_object()
    guild.get_thread = lambda _id: None
    client, queue = _coalescing_client(guild)
    state = client._connection  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(state, "_get_guild", lambda _id: guild)
    parse = state.parsers["THREAD_UPDATE"]
    active = {"guild_id": "9", "id": "7", "thread_metadata": {"archived": False}}
    archived = {"guild_id": "9", "id": "8", "thread_metadata": {"archived": True}}

    parse(archived)
    assert queue.empty()

    parse(active)
    assert parsed == [archived, active]
    assert _drain(queue) == [ThreadArchived(1, 7, False)]

    # 缓存里已有的线程由库自己派发 thread_update，这里不重复。
    guild.get_thread = lambda _id: object()
    parse(active)
    assert queue.empty()
    await client.close()


class GatedClock:
    """假时钟：sleep 不前进，等测试显式放行，这样能观察等待期间发生了什么。"""

    def __init__(self) -> None:
        self.wall = 1000.0
        self.mono = 50.0
        self.sleeps: list[float] = []
        self.parked = asyncio.Event()
        self.release = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.parked.set()
        await self.release.wait()
        self.release.clear()
        self.parked.clear()

    def guard(self) -> WakeGuard:
        guard = WakeGuard(lambda: self.wall, lambda: self.mono, self.sleep)
        guard.sample()
        return guard

    def suspend(self, seconds: float) -> None:
        self.wall += seconds

    def finish_settling(self) -> None:
        self.mono += SETTLE_SECONDS
        self.wall += SETTLE_SECONDS
        self.release.set()


def _closing_ws() -> FakeWs:
    return FakeWs(discord.ConnectionClosed(4004))  # pyright: ignore[reportArgumentType]


def _guarded_client(
    script: Script, clock: GatedClock
) -> tuple[HarvesterClient, list[dict[str, Any]], "asyncio.Queue[SupervisorEvent]"]:
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    calls: list[dict[str, Any]] = []
    client = HarvesterClient(1, queue, now, scripted_factory(script, calls), guard=clock.guard())
    return client, calls, queue


async def test_identify_waits_before_opening_a_socket() -> None:  # covers: AC-2
    clock = GatedClock()
    client, calls, queue = _guarded_client([returns(_closing_ws())], clock)
    clock.suspend(3600)

    task = asyncio.create_task(client.connect())
    await asyncio.wait_for(clock.parked.wait(), timeout=2)

    assert calls == []
    assert queue.empty()  # 没有 IdentifyRequested，也就不会写 discord_login_attempt。

    clock.finish_settling()
    with pytest.raises(discord.ConnectionClosed):
        await asyncio.wait_for(task, timeout=2)
    assert calls == [{"initial": True}]


async def test_resume_does_not_wait_but_rejected_resume_does() -> None:  # covers: AC-2, AC-3
    clock = GatedClock()
    first = FakeWs(ReconnectWebSocket(resume=True), close_code=1000)
    rejecting = FakeWs(ReconnectWebSocket(resume=False), None, None, close_code=1000)
    client, calls, queue = _guarded_client([returns(first), returns(rejecting)], clock)
    responder = asyncio.create_task(answer_retries(queue, [], 0, 0))

    # 第一次连接时还没睡过；断线之后才发现睡眠。
    def suspend_on_first_call(original: Any) -> Any:
        async def wrapped(c: HarvesterClient, **params: Any) -> Any:
            ws = await original(c, **params)
            clock.suspend(3600)
            return ws

        return wrapped

    client._ws_factory = suspend_on_first_call(client._ws_factory)
    task = asyncio.create_task(client.connect())
    await asyncio.wait_for(clock.parked.wait(), timeout=2)

    assert [c.get("resume", False) for c in calls] == [False, True]
    assert clock.sleeps  # RESUME 被拒之后的 IDENTIFY 才开始等。

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    responder.cancel()


async def test_no_sleep_means_no_wait() -> None:  # covers: AC-4
    clock = GatedClock()
    client, calls, _ = _guarded_client([returns(_closing_ws())], clock)

    with pytest.raises(discord.ConnectionClosed):
        await asyncio.wait_for(client.connect(), timeout=2)

    assert clock.sleeps == []
    assert calls == [{"initial": True}]


async def test_cancel_ends_the_wait_at_once() -> None:  # covers: AC-5
    clock = GatedClock()
    client, calls, _ = _guarded_client([], clock)
    clock.suspend(3600)

    task = asyncio.create_task(client.connect())
    await asyncio.wait_for(clock.parked.wait(), timeout=2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert calls == []


def test_new_client_passes_the_guard_on() -> None:
    guard = WakeGuard()
    client = new_client(1, asyncio.Queue(), now, guard=guard)
    assert client._guard is guard


# spec 0008：消息边界。


def fake_user(user_id: int = 5, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        name="kol",
        global_name="KOL",
        display_name="昵称",
        bot=bot,
        avatar=SimpleNamespace(key="hash"),
    )


def fake_message(**changes: Any) -> Any:
    fields: dict[str, Any] = {
        "id": 10,
        "guild": SimpleNamespace(id=1),
        "channel": SimpleNamespace(id=2),
        "author": fake_user(),
        "webhook_id": None,
        "reference": None,
        "message_snapshots": [],
        "type": SimpleNamespace(value=0),
        "content": "hello",
        "embeds": [],
        "edited_at": None,
        "attachments": [],
    }
    return SimpleNamespace(**{**fields, **changes})


def make_client(queue: "asyncio.Queue[SupervisorEvent]") -> HarvesterClient:
    return HarvesterClient(1, queue, lambda: datetime(2026, 1, 1, tzinfo=UTC))


def test_snapshot_converts_author_and_reply() -> None:  # covers: AC-1, AC-6
    snap = snapshot_message(
        fake_message(
            type=SimpleNamespace(value=19),
            reference=SimpleNamespace(type=discord.MessageReferenceType.reply, message_id=9),
        )
    )

    assert snap is not None
    assert (snap.channel_id, snap.guild_id, snap.reply_to_id) == (2, 1, 9)
    assert snap.author_display_name == "昵称"
    assert (snap.author.kind, snap.author.avatar_hash) == ("user", "hash")
    assert snap.attachments_known


def test_snapshot_marks_webhook_authors() -> None:  # covers: AC-1
    snap = snapshot_message(fake_message(webhook_id=99))

    assert snap is not None and snap.author.kind == "webhook"


def test_snapshot_skips_direct_messages() -> None:  # covers: AC-2
    assert snapshot_message(fake_message(guild=None)) is None


def test_snapshot_reads_forwarded_message() -> None:  # covers: AC-1
    embed = discord.Embed(title="t")
    snap = snapshot_message(
        fake_message(
            reference=SimpleNamespace(
                type=discord.MessageReferenceType.forward,
                message_id=3,
                channel_id=4,
                guild_id=None,
            ),
            message_snapshots=[
                SimpleNamespace(
                    content="fwd",
                    embeds=[embed],
                    attachments=[
                        SimpleNamespace(
                            id=9,
                            filename="a.png",
                            content_type="image/png",
                            size=5,
                            url="https://cdn.discordapp.com/attachments/4/9/a.png?ex=1",
                            width=2,
                            height=3,
                        )
                    ],
                )
            ],
        )
    )

    assert snap is not None and snap.forwarded is not None
    assert snap.forwarded["message_id"] == 3
    assert snap.forwarded["attachment_names"] == ["a.png"]
    assert [a.id for a in snap.forwarded_attachments] == [9]  # covers: spec 0011 AC-2
    assert snap.forwarded["embeds"][0].get("title") == "t"
    assert snap.reply_to_id is None


async def test_client_turns_off_default_subscriptions() -> None:  # covers: AC-9
    client = make_client(asyncio.Queue())

    assert client._connection._subscribe_guilds is False  # pyright: ignore[reportPrivateUsage]
    await client.close()


async def test_message_events_are_queued_in_order() -> None:  # covers: AC-1, AC-4
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    client = make_client(queue)

    client.dispatch("message", fake_message())
    client.dispatch("raw_message_edit", SimpleNamespace(message=fake_message(), data={"id": "10"}))
    client.dispatch("raw_message_delete", SimpleNamespace(channel_id=2, message_id=10))
    client.dispatch("raw_bulk_message_delete", SimpleNamespace(channel_id=2, message_ids={12, 11}))

    first = queue.get_nowait()
    edited = queue.get_nowait()
    deleted = queue.get_nowait()
    bulk = queue.get_nowait()
    assert isinstance(first, MessageReceived) and first.snapshot.attachments_known
    assert isinstance(edited, MessageEdited) and not edited.snapshot.attachments_known
    assert isinstance(deleted, MessagesDeleted) and deleted.message_ids == (10,)
    assert isinstance(bulk, MessagesDeleted) and bulk.message_ids == (11, 12)
    await client.close()


async def test_broken_message_is_skipped_without_raising() -> None:  # covers: AC-8
    queue: asyncio.Queue[SupervisorEvent] = asyncio.Queue()
    client = make_client(queue)

    client.dispatch("message", SimpleNamespace(guild=SimpleNamespace(id=1)))

    assert queue.empty()
    await client.close()


async def test_subscribe_skips_unknown_and_failing_guilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # covers: AC-9
    client = make_client(asyncio.Queue())
    calls: list[dict[str, bool]] = []

    async def subscribe(**kwargs: bool) -> None:
        calls.append(kwargs)

    async def failing(**_kwargs: bool) -> None:
        raise OSError

    guilds = {
        1: SimpleNamespace(unavailable=False, subscribe=subscribe),
        2: SimpleNamespace(unavailable=True, subscribe=subscribe),
        3: SimpleNamespace(unavailable=False, subscribe=failing),
    }
    monkeypatch.setattr(client, "get_guild", guilds.get)

    assert await client.subscribe_guilds(frozenset({1, 2, 3, 4})) == {1}
    assert calls == [{"typing": True, "threads": True}]
    await client.close()


async def test_thread_list_sync_schedules_a_snapshot() -> None:  # covers: AC-12
    client = make_client(asyncio.Queue())
    guild_id = 321

    client._connection.parsers["THREAD_LIST_SYNC"](
        {"guild_id": str(guild_id), "threads": [], "members": []}
    )  # pyright: ignore[reportPrivateUsage]

    assert guild_id in client._pending_guilds  # pyright: ignore[reportPrivateUsage]
    await client.close()


# ---- 历史消息边界函数（spec 0012 AC-12）--------------------------------------------------


class FakeHistoryChannel(discord.abc.Messageable):
    """`history` 直接抛出给定的异常，或者返回空列表。"""

    def __init__(self, error: BaseException | None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def _get_channel(self) -> Any:
        return self

    async def history(self, **kwargs: Any) -> Any:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if False:  # 让它成为异步生成器
            yield None


def history_client(channel: Any, *, cached: bool = True) -> Any:
    async def fetch_channel(_channel_id: int) -> Any:
        return channel

    return SimpleNamespace(
        get_channel=lambda _channel_id: channel if cached else None, fetch_channel=fetch_channel
    )


def cloudflare_ban() -> discord.HTTPException:
    return discord.HTTPException(FakeResponse(429), "Cloudflare ban (code: 1015)")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (discord.Forbidden(FakeResponse(403), "no"), Forbidden()),  # pyright: ignore[reportArgumentType]
        (discord.NotFound(FakeResponse(404), "gone"), NotFound()),  # pyright: ignore[reportArgumentType]
        (discord.DiscordServerError(FakeResponse(502), "x"), HistoryUnavailable("http_502")),  # pyright: ignore[reportArgumentType]
        (http_error(401), AccountRisk("unauthorized")),
        (cloudflare_ban(), AccountRisk("cloudflare_ban")),
        (http_error(400), Unexpected("HTTPException_400")),
        (OSError(), HistoryUnavailable("network")),
        (TimeoutError(), HistoryUnavailable("network")),
        (ValueError(), Unexpected("ValueError")),
    ],
)
async def test_fetch_history_page_maps_errors(error: BaseException, expected: object) -> None:
    channel = FakeHistoryChannel(error)
    assert await fetch_history_page(history_client(channel), 201, 999) == expected


async def test_fetch_history_page_rate_limited_and_counts_requests() -> None:
    limited = await fetch_history_page(
        history_client(FakeHistoryChannel(discord.RateLimited(12.5))), 201, 999
    )
    assert limited == HistoryRateLimited(12.5)

    channel = FakeHistoryChannel(None)
    page = await fetch_history_page(history_client(channel, cached=False), 201, 999)
    assert page == Fetched(messages=(), returned_ids=(), raw_count=0, request_count=2)
    assert channel.calls[0]["limit"] == 100 and channel.calls[0]["before"].id == 999
    assert channel.calls[0]["oldest_first"] is False
