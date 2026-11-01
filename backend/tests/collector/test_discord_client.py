"""HarvesterClient 的边界：RESUME 判定、失败转换、socket 释放。Discord 用假的 from_client。"""

import ast
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import discord
import pytest
from discord.gateway import ReconnectWebSocket

import harvester
from harvester.collector.discord_client import HarvesterClient, reconnect_failure, to_failure
from harvester.collector.events import (
    Disconnected,
    IdentifyRequested,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
)
from harvester.collector.failures import ConnectionFailure


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
