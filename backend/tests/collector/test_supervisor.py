"""监督循环的集成测试：真实 PostgreSQL，假的 Discord 会话（spec 0005）。"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DataError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.testing import CapturingLogger

from harvester.collector import supervisor
from harvester.collector.discord_client import HarvesterClient
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
    Resumed,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
    ThreadAccessLost,
    ThreadArchived,
)
from harvester.collector.failures import ConnectionFailure
from harvester.collector.supervisor import (
    Deps,
    GatewaySession,
    LoopState,
    SessionState,
    UnexpectedFailureError,
    handle_event,
    run_supervisor,
)
from harvester.collector.wake_guard import WakeGuard
from harvester.crypto import PURPOSE_DISCORD_TOKEN, encrypt
from harvester.db import account as accounts
from harvester.db.account import AccountRow
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    DiscordAccount,
    DiscordLoginAttempt,
    Guild,
    Message,
)
from harvester.db.raw import MESSAGE_CHANGED_CHANNEL, NEW_MESSAGE_CHANNEL
from harvester.db.snapshots import AccessSnapshot, ChannelSnapshot, GuildState
from tests.api.conftest import crypto_settings
from tests.db.builders import CATEGORY, CHANNEL, GUILD, MESSAGE_ID, at, message, seed_channel

Factory = async_sessionmaker[AsyncSession]
TOKEN = "fake-discord-token.abc123.XYZ"
USER_ID = 111
KEYS = crypto_settings().key_ring
Queue = asyncio.Queue[SupervisorEvent]
# READY 之后 FullSync 带的服务器：和 seed_channel 种下的数据一致，否则它们会被判成已退出。
GUILD_STATE = GuildState(GUILD, (CATEGORY, CHANNEL), (AccessSnapshot(CHANNEL.id, True),))


@dataclass
class FakeClock:
    now: datetime = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeSession:
    """按测试的指令发事件。默认 login 成功后发 IDENTIFY 请求，通过就 READY。"""

    generation: int
    queue: Queue
    user_id: int
    tokens: list[str]
    states: list[GuildState]
    closed: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    subscribe_calls: list[frozenset[int]] = field(default_factory=list[frozenset[int]])
    # 不在缓存里的服务器：订阅不算发出去。
    unavailable: frozenset[int] = frozenset()

    async def run_session(self, token: str) -> None:
        self.tokens.append(token)
        reply: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(LoggedIn(self.generation, self.user_id, "name", reply))
        if not await reply:
            self.queue.put_nowait(SessionEnded(self.generation))
            return
        if not await self.identify():
            self.queue.put_nowait(SessionEnded(self.generation))
            return
        self.queue.put_nowait(Ready(self.generation, required_action=False))
        self.queue.put_nowait(self.full_sync())
        await self.finished.wait()

    def full_sync(self) -> FullSync:
        return FullSync(self.generation, tuple(self.states), frozenset())

    async def identify(self) -> bool:
        reply: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(IdentifyRequested(self.generation, reply))
        return await reply

    async def retry(
        self,
        failure: ConnectionFailure,
        *,
        immediate_allowed: bool = False,
        resume_ready: bool = False,
        resume_rejected: bool = False,
    ) -> float | None:
        reply: asyncio.Future[float | None] = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(
            RetryNeeded(
                self.generation, failure, immediate_allowed, reply, resume_ready, resume_rejected
            )
        )
        return await reply

    async def subscribe_guilds(self, guild_ids: frozenset[int]) -> frozenset[int]:
        self.subscribe_calls.append(guild_ids)
        return guild_ids - self.unavailable

    async def close(self) -> None:
        self.closed = True


@dataclass
class Harness:
    factory: Factory
    clock: FakeClock
    deps: Deps
    sessions: list[FakeSession]
    tokens: list[str]
    user_id: list[int]
    states: list[GuildState]
    task: "asyncio.Task[None] | None" = None

    def start(self) -> None:
        self.task = asyncio.create_task(run_supervisor(self.deps))

    @property
    def current(self) -> FakeSession:
        return self.sessions[-1]

    async def read(self) -> AccountRow:
        async with self.factory() as session:
            row = await accounts.get_account(session)
        assert row is not None
        return row

    async def wait_until(self, check: Callable[[], Awaitable[bool]]) -> None:
        async with asyncio.timeout(5):
            while not await check():  # noqa: ASYNC110  # 等的是数据库状态，没有可以等的 Event。
                await asyncio.sleep(0.01)

    async def wait_status(self, status: str) -> AccountRow:
        async def matches() -> bool:
            return (await self.read()).status == status

        await self.wait_until(matches)
        return await self.read()

    async def wait_spans(self, count: int) -> None:
        async def reached() -> bool:
            return len(await self.spans()) >= count

        await self.wait_until(reached)

    async def attempts(self) -> int:
        async with self.factory() as session:
            count = await session.scalar(select(text("count(*)")).select_from(DiscordLoginAttempt))
        return int(count or 0)

    async def api(self, write: Callable[[AsyncSession], Awaitable[object]]) -> None:
        async with self.factory() as session, session.begin():
            await write(session)
        self.deps.wake.set()

    async def seed_collecting(self) -> None:
        """种下一个开了采集、读得了的频道；之后的 FullSync 会带上它所在的服务器。"""
        row = await self.read()
        async with self.factory() as session, session.begin():
            await seed_channel(session)
            await session.execute(
                update(Channel)
                .where(Channel.id == CHANNEL.id)
                .values(collector_account_id=row.id, collect_enabled=True)
            )
            await session.execute(
                insert(AccountChannelAccess).values(
                    account_id=row.id, channel_id=CHANNEL.id, can_read=True
                )
            )
        self.states.append(GUILD_STATE)

    async def spans(self) -> list[object]:
        async with self.factory() as session:
            return list(
                await session.scalars(select(ChannelCoverage.span).order_by(ChannelCoverage.id))
            )

    async def add_attempts(self, count: int, hours_ago: float = 1) -> None:
        row = await self.read()
        async with self.factory() as session, session.begin():
            for _ in range(count):
                await session.execute(
                    insert(DiscordLoginAttempt).values(
                        account_id=row.id,
                        attempted_at=text(f"now() - interval '{hours_ago} hours'"),
                    )
                )


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def harness(engine: AsyncEngine) -> AsyncIterator[Harness]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock()
    sessions: list[FakeSession] = []
    tokens: list[str] = []
    user_id = [USER_ID]
    states: list[GuildState] = []

    def new_session(generation: int, queue: Queue, _clock: object) -> GatewaySession:
        session = FakeSession(generation, queue, user_id[0], tokens, states)
        sessions.append(session)
        return session

    deps = Deps(
        factory=factory,
        key_ring=KEYS,
        new_session=new_session,
        clock=clock,
        uniform=lambda _low, _high: 0.001,  # 退避压到几毫秒，测试不用真的等。
        wake=asyncio.Event(),
        channels_wake=asyncio.Event(),
        heartbeat_interval=3600,
        crash_guard_seconds=0,
        sleep=_no_sleep,
    )
    h = Harness(factory, clock, deps, sessions, tokens, user_id, states)
    yield h
    if h.task is not None:
        h.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await h.task


async def add_account(h: Harness, token: str = TOKEN) -> AccountRow:
    async with h.factory() as session, session.begin():
        row = await accounts.create_account(
            session, "main", encrypt(KEYS, PURPOSE_DISCORD_TOKEN, token.encode()), 1
        )
    assert row != "exists"
    return row


async def test_idles_without_account_then_connects(harness: Harness) -> None:  # covers: AC-1, AC-2
    harness.start()
    await asyncio.sleep(0.05)
    assert harness.sessions == []

    await add_account(harness)
    harness.deps.wake.set()

    row = await harness.wait_status("online")
    assert harness.tokens == [TOKEN]
    assert (row.discord_user_id, row.discord_username) == (USER_ID, "name")
    assert row.last_heartbeat_at is not None
    assert await harness.attempts() == 1


async def test_exhausted_budget_pauses_without_any_request(harness: Harness) -> None:  # AC-7
    await add_account(harness)
    await harness.add_attempts(10)
    harness.start()

    row = await harness.wait_status("login_paused")

    assert harness.sessions == []
    assert row.last_error == "login_budget_exhausted"
    assert row.retry_at is not None and row.retry_at > row.db_now


async def test_restart_and_token_change_stay_paused(harness: Harness) -> None:  # covers: AC-7
    await add_account(harness)
    await harness.add_attempts(10)
    harness.start()
    await harness.wait_status("login_paused")

    await harness.api(lambda s: accounts.replace_token(s, b"bad", 1))
    row = await harness.wait_status("token_invalid")  # 密文解不开，不发请求。
    assert row.last_error == "token_undecryptable"

    cipher = encrypt(KEYS, PURPOSE_DISCORD_TOKEN, b"new-token-value-1234567890")
    await harness.api(lambda s: accounts.replace_token(s, cipher, 1))
    await harness.wait_status("login_paused")
    assert harness.sessions == []


async def test_paused_resumes_when_window_passes(harness: Harness) -> None:  # covers: AC-7
    row = await add_account(harness)
    async with harness.factory() as session, session.begin():
        await accounts.record_status(
            session, row.id, 1, "login_paused", "login_budget_exhausted", at(0)
        )
    harness.start()

    await harness.wait_status("online")


async def test_budget_race_at_identify_goes_to_paused(harness: Harness) -> None:  # covers: AC-7
    await add_account(harness)
    await harness.add_attempts(9)
    harness.start()
    await harness.wait_status("online")
    assert await harness.attempts() == 10

    session = harness.current
    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    await harness.wait_status("reconnecting")
    assert not await session.identify()

    row = await harness.wait_status("login_paused")
    assert await harness.attempts() == 10
    assert row.retry_at is not None
    assert session.closed


async def test_resume_costs_no_budget_and_keeps_coverage(harness: Harness) -> None:  # covers: AC-6
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    await harness.seed_collecting()
    session = harness.current

    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    await harness.wait_status("reconnecting")
    session.queue.put_nowait(Resumed(session.generation))
    await harness.wait_status("online")

    assert await harness.attempts() == 1


async def test_reidentify_closes_coverage_at_disconnect_time(harness: Harness) -> None:  # AC-6
    await add_account(harness)
    await harness.add_attempts(0)
    # 先有频道，READY 时才会打开覆盖段。
    await harness.seed_collecting()
    harness.start()
    await harness.wait_status("online")
    opened_at = harness.clock()
    session = harness.current

    disconnect_at = opened_at + timedelta(seconds=30)
    harness.clock.advance(100)
    session.queue.put_nowait(Disconnected(session.generation, disconnect_at))
    await harness.wait_status("reconnecting")
    assert await session.identify()
    assert await harness.attempts() == 2
    session.queue.put_nowait(Ready(session.generation, required_action=False))
    session.queue.put_nowait(session.full_sync())
    await harness.wait_status("online")

    await harness.wait_spans(2)
    spans = await harness.spans()
    assert len(spans) == 2
    assert spans[0].upper == disconnect_at  # type: ignore[attr-defined]
    assert spans[1].upper is None and spans[1].lower == harness.clock()  # type: ignore[attr-defined]


async def test_disable_during_outage_closes_at_disconnect_time(
    harness: Harness,
) -> None:  # AC-6
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    disconnect_at = harness.clock() + timedelta(seconds=10)
    harness.clock.advance(500)
    session.queue.put_nowait(Disconnected(session.generation, disconnect_at))
    await harness.wait_status("reconnecting")

    await harness.api(lambda s: accounts.set_enabled(s, False))

    async def closed_at_disconnect() -> bool:
        spans = await harness.spans()
        return spans[0].upper == disconnect_at  # type: ignore[attr-defined]

    await harness.wait_until(closed_at_disconnect)
    assert session.closed
    assert (await harness.read()).status == "disabled"


async def test_disabled_never_logs_in_after_restart(harness: Harness) -> None:  # covers: AC-12
    await add_account(harness)
    await harness.api(lambda s: accounts.set_enabled(s, False))
    harness.start()
    await asyncio.sleep(0.05)
    assert harness.sessions == []

    await harness.api(lambda s: accounts.set_enabled(s, True))
    await harness.wait_status("online")


@pytest.mark.parametrize(
    ("failure", "status", "error"),
    [
        (ConnectionFailure("login_failure", http_status=401), "token_invalid", "http_401"),
        (ConnectionFailure("captcha"), "verification_required", "captcha_required"),
        (
            ConnectionFailure("http", http_status=403, during_login=True),
            "verification_required",
            "http_403",
        ),
    ],
)
async def test_stop_class_failure_stops_logging_in(
    harness: Harness, failure: ConnectionFailure, status: str, error: str
) -> None:  # covers: AC-8, AC-9
    await add_account(harness)
    original = harness.deps.new_session

    def failing(generation: int, queue: Queue, clock: object) -> GatewaySession:
        session = original(generation, queue, clock)  # pyright: ignore[reportArgumentType]
        assert isinstance(session, FakeSession)

        async def run(_token: str) -> None:
            assert await session.retry(failure) is None
            queue.put_nowait(SessionEnded(generation))

        session.run_session = run  # pyright: ignore[reportAttributeAccessIssue]
        return session

    harness.deps = Deps(**{**harness.deps.__dict__, "new_session": failing})
    harness.start()

    row = await harness.wait_status(status)

    assert row.last_error == error
    await asyncio.sleep(0.05)
    assert len(harness.sessions) == 1

    await harness.api(accounts.request_reconnect)
    await harness.wait_until(lambda: _session_count_at_least(harness, 2))


async def _session_count_at_least(harness: Harness, count: int) -> bool:
    return len(harness.sessions) >= count


async def test_4004_while_online_goes_to_token_invalid(harness: Harness) -> None:  # covers: AC-8
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    session.queue.put_nowait(
        SessionEnded(session.generation, ConnectionFailure("gateway_closed", close_code=4004))
    )

    row = await harness.wait_status("token_invalid")
    assert row.last_error == "gateway_close_4004"
    await asyncio.sleep(0.05)
    assert len(harness.sessions) == 1


async def test_unrecoverable_close_makes_a_new_client(harness: Harness) -> None:  # covers: AC-6
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    first = harness.current

    first.queue.put_nowait(Disconnected(first.generation, harness.clock()))
    first.queue.put_nowait(
        SessionEnded(first.generation, ConnectionFailure("gateway_closed", close_code=4013))
    )

    await harness.wait_until(lambda: _session_count_at_least(harness, 2))
    assert first.closed
    await harness.wait_status("online")
    assert await harness.attempts() == 2


async def test_required_action_stops_logging_in(harness: Harness) -> None:  # covers: AC-9
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    from harvester.collector.events import RequiredAction

    session.queue.put_nowait(RequiredAction(session.generation))

    row = await harness.wait_status("verification_required")
    assert row.last_error == "required_action"
    await asyncio.sleep(0.05)
    assert session.closed
    assert len(harness.sessions) == 1


async def test_required_action_in_ready_never_goes_online(harness: Harness) -> None:  # AC-9
    await add_account(harness)
    original = harness.deps.new_session

    def needs_action(generation: int, queue: Queue, clock: object) -> GatewaySession:
        session = original(generation, queue, clock)  # pyright: ignore[reportArgumentType]

        async def run(_token: str) -> None:
            queue.put_nowait(Ready(generation, required_action=True))

        session.run_session = run  # pyright: ignore[reportAttributeAccessIssue]
        return session

    harness.deps = Deps(**{**harness.deps.__dict__, "new_session": needs_action})
    harness.start()

    row = await harness.wait_status("verification_required")
    assert row.last_heartbeat_at is None


async def test_token_change_same_identity_returns_online(harness: Harness) -> None:  # covers: AC-10
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    first = harness.current

    cipher = encrypt(KEYS, PURPOSE_DISCORD_TOKEN, b"second-token-1234567890")
    await harness.api(lambda s: accounts.replace_token(s, cipher, 1))

    await harness.wait_until(lambda: _session_count_at_least(harness, 2))
    await harness.wait_status("online")
    assert first.closed
    assert harness.tokens[-1] == "second-token-1234567890"


async def test_token_of_other_account_stops_before_identify(harness: Harness) -> None:  # AC-10, 11
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    assert await harness.attempts() == 1

    harness.user_id[0] = 222
    cipher = encrypt(KEYS, PURPOSE_DISCORD_TOKEN, b"other-account-token-123456")
    await harness.api(lambda s: accounts.replace_token(s, cipher, 1))

    row = await harness.wait_status("account_mismatch")
    assert row.discord_user_id == USER_ID
    assert row.last_error == "account_mismatch"
    assert await harness.attempts() == 1

    await harness.api(accounts.switch_identity)
    row = await harness.wait_status("online")
    assert row.discord_user_id == 222


async def test_stale_status_write_ends_session_immediately(harness: Harness) -> None:  # AC-13
    """先读版本，再由 api 停用，然后 collector 写 reconnecting。"""
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    async with harness.factory() as db, db.begin():
        await accounts.set_enabled(db, False)
    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))

    async def closed() -> bool:
        return session.closed

    await harness.wait_until(closed)
    assert (await harness.read()).status == "disabled"


async def test_events_from_old_generation_are_dropped(harness: Harness) -> None:  # covers: AC-13
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    session.queue.put_nowait(Disconnected(session.generation - 1, harness.clock()))
    session.queue.put_nowait(Ready(session.generation - 1, required_action=False))
    await asyncio.sleep(0.05)

    assert (await harness.read()).status == "online"


async def test_retry_writes_reconnecting_and_returns_backoff(harness: Harness) -> None:  # AC-5
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    harness.clock.advance(1)

    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    first = await session.retry(ConnectionFailure("network"))
    second = await session.retry(ConnectionFailure("rate_limited", retry_after=99.0))

    row = await harness.read()
    assert (row.status, row.last_error) == ("reconnecting", "http_429")
    assert first == pytest.approx(5 * 0.001)
    assert second == 99.0
    assert await harness.attempts() == 1


async def test_network_resume_retries_cap_at_30_seconds(harness: Harness) -> None:
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    network = ConnectionFailure("network")

    delays = [await session.retry(network, resume_ready=True) for _ in range(5)]
    identify_delay = await session.retry(network)

    assert delays == pytest.approx([d * 0.001 for d in (5, 10, 20, 30, 30)])
    # 同一个计数继续往上走，但要重新 IDENTIFY 的重试不受 30 秒封顶影响。
    assert identify_delay == pytest.approx(160 * 0.001)


async def test_rejected_resume_caps_only_the_first_wait(harness: Harness) -> None:  # covers: AC-5
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    network = ConnectionFailure("network")

    for _ in range(6):
        await session.retry(network, resume_ready=True)
    rejected = await session.retry(ConnectionFailure("gateway_reconnect"), resume_rejected=True)
    identify_retry = await session.retry(network)

    assert rejected == pytest.approx(30 * 0.001)
    assert identify_retry == pytest.approx(300 * 0.001)


async def test_stable_connection_reconnects_immediately_and_resets(
    harness: Harness,
) -> None:  # AC-5
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    await session.retry(ConnectionFailure("network"))  # attempt 变成 1。
    session.queue.put_nowait(Resumed(session.generation))
    await harness.wait_status("online")

    harness.clock.advance(61)
    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    immediate = await session.retry(ConnectionFailure("gateway_reconnect"), immediate_allowed=True)
    assert immediate == 0.0

    # 服务端不让 RESUME 时总是走退避。
    session.queue.put_nowait(Resumed(session.generation))
    await harness.wait_status("online")
    harness.clock.advance(301)
    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    delay = await session.retry(ConnectionFailure("network"))
    assert delay == pytest.approx(5 * 0.001)  # 稳定满 5 分钟，退避从第一档重新开始。


async def test_unexpected_failure_crashes_the_loop(harness: Harness) -> None:
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    session.queue.put_nowait(SessionEnded(session.generation, unexpected=ValueError("boom")))

    assert harness.task is not None
    with pytest.raises(ValueError, match="boom"):
        await asyncio.wait_for(harness.task, timeout=2)
    assert session.closed
    harness.task = None


async def test_unclassified_http_failure_crashes(harness: Harness) -> None:
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    session.queue.put_nowait(
        SessionEnded(session.generation, ConnectionFailure("http", http_status=400))
    )

    assert harness.task is not None
    with pytest.raises(UnexpectedFailureError):
        await asyncio.wait_for(harness.task, timeout=2)
    harness.task = None


async def test_cancel_closes_client_and_coverage_without_status_change(
    harness: Harness,
) -> None:  # covers: AC-17
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_status("online")
    await harness.wait_spans(1)
    session = harness.current
    harness.clock.advance(50)

    assert harness.task is not None
    harness.task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await harness.task
    harness.task = None

    assert session.closed
    assert (await harness.read()).status == "online"
    spans = await harness.spans()
    assert spans[0].upper == harness.clock()  # type: ignore[attr-defined]


async def test_startup_normalizes_status_and_closes_stale_coverage(harness: Harness) -> None:
    row = await add_account(harness)
    async with harness.factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collector_account_id=row.id, collect_enabled=True)
        )
        await session.execute(
            insert(ChannelCoverage).values(
                channel_id=CHANNEL.id,
                span=text("tstzrange(now() - interval '1 hour', NULL, '[)')"),
                source="realtime",
            )
        )
        await accounts.record_status(session, row.id, 1, "online", None)
    harness.start()

    await harness.wait_until(lambda: _session_count_at_least(harness, 1))
    await harness.wait_status("online")
    spans = await harness.spans()
    assert spans[0].upper is not None  # type: ignore[attr-defined]


async def test_small_retry_after_never_shortens_the_backoff(harness: Harness) -> None:  # AC-5
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")

    delay = await harness.current.retry(ConnectionFailure("rate_limited", retry_after=0.0001))

    assert delay == pytest.approx(5 * 0.001)


async def test_http_500_keeps_five_minute_cap_and_stays_retrying(harness: Harness) -> None:  # AC-5
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    failure = ConnectionFailure("http", http_status=503)

    delays = [await session.retry(failure, resume_ready=True) for _ in range(8)]

    assert delays[-2:] == pytest.approx([300 * 0.001, 300 * 0.001])
    row = await harness.read()
    assert (row.status, row.last_error) == ("reconnecting", "http_5xx")
    assert await harness.attempts() == 1


async def test_long_outage_of_retries_writes_no_budget_rows(harness: Harness) -> None:  # AC-7
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current

    for _ in range(25):
        await session.retry(ConnectionFailure("network"), resume_ready=True)

    assert await harness.attempts() == 1


async def test_label_change_keeps_the_session(harness: Harness) -> None:  # covers: AC-12
    await add_account(harness)
    harness.start()
    before = await harness.wait_status("online")

    await harness.api(lambda s: accounts.update_label(s, "renamed"))
    await asyncio.sleep(0.05)

    row = await harness.read()
    assert (row.status, row.config_version) == ("online", before.config_version)
    assert len(harness.sessions) == 1
    assert not harness.current.closed


async def test_disable_closes_session_then_enable_connects_again(harness: Harness) -> None:  # AC-12
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    first = harness.current

    await harness.api(lambda s: accounts.set_enabled(s, False))
    await harness.wait_until(lambda: _is_closed(first))
    assert (await harness.read()).status == "disabled"
    assert len(harness.sessions) == 1

    await harness.api(lambda s: accounts.set_enabled(s, True))
    await harness.wait_status("online")
    assert len(harness.sessions) == 2


async def test_token_change_while_disabled_only_saves(harness: Harness) -> None:  # covers: AC-12
    await add_account(harness)
    async with harness.factory() as session, session.begin():
        await accounts.set_enabled(session, False)
    harness.start()
    await asyncio.sleep(0.05)

    cipher = encrypt(KEYS, PURPOSE_DISCORD_TOKEN, b"new-token-value-1234567890")
    await harness.api(lambda s: accounts.replace_token(s, cipher, 1))
    await asyncio.sleep(0.05)

    assert harness.sessions == []
    assert (await harness.read()).status == "disabled"
    assert await harness.attempts() == 0


async def test_reconnect_after_stop_class_logs_in_again(harness: Harness) -> None:  # AC-8, AC-9
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    session.queue.put_nowait(
        SessionEnded(session.generation, ConnectionFailure("login_failure", http_status=401))
    )
    await harness.wait_status("token_invalid")
    assert len(harness.sessions) == 1

    await harness.api(accounts.request_reconnect)

    await harness.wait_status("online")
    assert len(harness.sessions) == 2


async def test_undecryptable_token_sends_no_request(harness: Harness) -> None:
    await add_account(harness)
    other_key = encrypt(KEYS, "another_purpose", TOKEN.encode())
    await harness.api(lambda s: accounts.replace_token(s, other_key, 1))
    harness.start()

    row = await harness.wait_status("token_invalid")

    assert row.last_error == "token_undecryptable"
    assert harness.sessions == []
    assert await harness.attempts() == 0


async def test_heartbeat_ticks_while_online(harness: Harness) -> None:  # covers: AC-15
    await add_account(harness)
    harness.deps = replace(harness.deps, heartbeat_interval=0.01)
    harness.start()
    first = await harness.wait_status("online")

    async def moved() -> bool:
        return (await harness.read()).last_heartbeat_at != first.last_heartbeat_at

    await harness.wait_until(moved)


async def test_failed_heartbeat_write_keeps_the_session(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_account(harness)
    calls: list[int] = []

    async def failing(*_args: object) -> None:
        calls.append(1)
        raise SQLAlchemyError("down")

    monkeypatch.setattr(accounts, "record_heartbeat", failing)
    harness.deps = replace(harness.deps, heartbeat_interval=0.01)
    harness.start()
    await harness.wait_status("online")

    async def ticked() -> bool:
        return len(calls) >= 2

    await harness.wait_until(ticked)

    assert harness.task is not None and not harness.task.done()
    assert not harness.current.closed


async def test_crash_guard_delays_login_after_a_recent_attempt(harness: Harness) -> None:
    await add_account(harness)
    await harness.add_attempts(1, hours_ago=0)
    harness.deps = replace(harness.deps, crash_guard_seconds=0.5)
    harness.start()

    await asyncio.sleep(0.1)
    assert harness.sessions == []

    await harness.wait_status("online")


async def test_crash_guard_skipped_when_last_attempt_is_old(harness: Harness) -> None:
    await add_account(harness)
    await harness.add_attempts(1, hours_ago=1)
    harness.deps = replace(harness.deps, crash_guard_seconds=60)
    harness.start()

    await harness.wait_status("online")  # 5 秒内就上线，说明没有等 60 秒。


async def test_startup_keeps_stopped_status(harness: Harness) -> None:  # covers: AC-8
    row = await add_account(harness)
    async with harness.factory() as session, session.begin():
        await accounts.record_status(session, row.id, 1, "token_invalid", "http_401")
    harness.start()
    await asyncio.sleep(0.05)

    current = await harness.read()
    assert (current.status, current.last_error) == ("token_invalid", "http_401")
    assert harness.sessions == []


async def _is_closed(session: FakeSession) -> bool:
    return session.closed


async def test_full_sync_records_time_and_opens_coverage(harness: Harness) -> None:
    # covers: spec 0006 AC-1, AC-7, AC-11
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()

    await harness.wait_spans(1)

    async with harness.factory() as session:
        synced = await session.scalar(select(DiscordAccount.channels_synced_at))
    assert synced == harness.clock()
    assert (await harness.spans())[0].upper is None  # type: ignore[attr-defined]


async def test_channels_wake_aligns_without_reconnecting(harness: Harness) -> None:
    # covers: spec 0006 AC-7
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    await harness.seed_collecting()

    harness.deps.channels_wake.set()

    await harness.wait_spans(1)
    assert len(harness.sessions) == 1
    assert await harness.attempts() == 1


async def test_heartbeat_aligns_when_the_notification_was_lost(harness: Harness) -> None:
    # covers: spec 0006 AC-7
    await add_account(harness)
    harness.deps = replace(harness.deps, heartbeat_interval=0.02)
    harness.start()
    await harness.wait_status("online")

    await harness.seed_collecting()

    await harness.wait_spans(1)


async def test_no_alignment_while_reconnecting(harness: Harness) -> None:
    # covers: spec 0006 AC-7
    await add_account(harness)
    harness.start()
    await harness.wait_status("online")
    session = harness.current
    session.queue.put_nowait(Disconnected(session.generation, harness.clock()))
    await harness.wait_status("reconnecting")
    await harness.seed_collecting()

    harness.deps.channels_wake.set()
    await asyncio.sleep(0.05)
    assert await harness.spans() == []

    session.queue.put_nowait(Resumed(session.generation))
    await harness.wait_spans(1)


async def test_stale_full_sync_after_identity_switch_writes_nothing(harness: Harness) -> None:
    # covers: spec 0006 AC-11
    row = await add_account(harness)
    async with harness.factory() as session, session.begin():
        await session.execute(update(DiscordAccount).values(config_version=2))
    state = SessionState(loop=LoopState(generation=1), status="online")

    after = await handle_event(harness.deps, row, state, FullSync(1, (GUILD_STATE,), frozenset()))

    assert after.done
    async with harness.factory() as session:
        assert await session.scalar(select(DiscordAccount.channels_synced_at)) is None
        assert await session.scalar(select(func.count()).select_from(Guild)) == 0


async def _synced_online(harness: Harness) -> None:
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_spans(1)


async def _open_count(harness: Harness) -> int:
    async with harness.factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ChannelCoverage)
            .where(func.upper_inf(ChannelCoverage.span))
        )
    return int(count or 0)


async def _wait_open_count(harness: Harness, expected: int) -> None:
    async def reached() -> bool:
        return await _open_count(harness) == expected

    await harness.wait_until(reached)


async def test_incremental_events_close_coverage_and_keep_the_switch(harness: Harness) -> None:
    # covers: spec 0006 AC-2, AC-8
    await _synced_online(harness)
    session = harness.current
    lost = replace(GUILD_STATE, access=(AccessSnapshot(CHANNEL.id, False),))

    session.queue.put_nowait(GuildChanged(session.generation, lost))
    await _wait_open_count(harness, 0)
    async with harness.factory() as db:
        assert await db.scalar(select(Channel.collect_enabled).where(Channel.id == CHANNEL.id))

    session.queue.put_nowait(GuildChanged(session.generation, GUILD_STATE))
    await _wait_open_count(harness, 1)

    session.queue.put_nowait(ChannelRemoved(session.generation, CHANNEL.id))
    await _wait_open_count(harness, 0)


async def test_guild_removed_closes_coverage(harness: Harness) -> None:  # covers: spec 0006 AC-8
    await _synced_online(harness)
    session = harness.current

    session.queue.put_nowait(GuildRemoved(session.generation, GUILD.id))

    await _wait_open_count(harness, 0)
    async with harness.factory() as db:
        assert await db.scalar(select(Guild.status).where(Guild.id == GUILD.id)) == "left"


async def test_thread_events_reach_the_database(harness: Harness) -> None:
    # covers: spec 0006 AC-2, AC-8
    thread = ChannelSnapshot(
        id=205, guild_id=GUILD.id, parent_id=CHANNEL.id, type=11, name="t", position=None
    )
    state = replace(
        GUILD_STATE,
        channels=(*GUILD_STATE.channels, thread),
        access=(*GUILD_STATE.access, AccessSnapshot(thread.id, True)),
    )
    await _synced_online(harness)
    session = harness.current
    session.queue.put_nowait(GuildChanged(session.generation, state))

    async def thread_written() -> bool:
        async with harness.factory() as db:
            return await db.scalar(select(Channel.id).where(Channel.id == thread.id)) is not None

    await harness.wait_until(thread_written)
    row = await harness.read()
    async with harness.factory() as db, db.begin():
        await db.execute(
            update(Channel)
            .where(Channel.id == thread.id)
            .values(collect_enabled=True, collector_account_id=row.id)
        )
    harness.deps.channels_wake.set()
    await _wait_open_count(harness, 2)

    session.queue.put_nowait(ThreadAccessLost(session.generation, thread.id))
    await _wait_open_count(harness, 1)

    session.queue.put_nowait(GuildChanged(session.generation, state))
    await _wait_open_count(harness, 2)
    session.queue.put_nowait(ThreadArchived(session.generation, thread.id, True))
    await _wait_open_count(harness, 1)


async def test_every_incremental_write_updates_sync_time(harness: Harness) -> None:
    # covers: spec 0006 AC-11
    await _synced_online(harness)
    session = harness.current
    harness.clock.advance(60)
    expected = harness.clock()

    session.queue.put_nowait(GuildChanged(session.generation, GUILD_STATE))

    async def updated() -> bool:
        async with harness.factory() as db:
            return await db.scalar(select(DiscordAccount.channels_synced_at)) == expected

    await harness.wait_until(updated)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> CapturingLogger:
    # 不用 capture_logs：别的测试调过 configure_logging 后，模块级 logger 可能已缓存旧配置。
    logger = CapturingLogger()
    monkeypatch.setattr(supervisor, "log", logger)
    return logger


def _logged(logger: CapturingLogger, event: str) -> list[dict[str, object]]:
    return [c.kwargs for c in logger.calls if c.args == (event,)]


@pytest.mark.parametrize(
    ("resume_rejected", "disconnected_at", "reason"),
    [
        (True, at(0), "resume_rejected"),
        (True, None, "resume_rejected"),
        (False, at(0), "after_disconnect"),
        (False, None, "initial"),
    ],
)
async def test_identify_reservation_logs_its_reason(  # covers: AC-11
    harness: Harness,
    captured: CapturingLogger,
    resume_rejected: bool,
    disconnected_at: datetime | None,
    reason: str,
) -> None:
    row = await add_account(harness)
    # 代号 2：断线之后新建的 client，断线时间是跨会话留下来的。
    state = SessionState(LoopState(generation=2, disconnected_at=disconnected_at), "reconnecting")
    reply: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    await handle_event(harness.deps, row, state, IdentifyRequested(2, reply, resume_rejected))

    assert reply.result() is True
    assert _logged(captured, "collector.identify_reserved") == [{"reason": reason}]


async def test_exhausted_identify_logs_retry_at_and_keeps_coverage_rule(  # covers: AC-6, AC-11
    harness: Harness, captured: CapturingLogger
) -> None:
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_status("online")
    await harness.wait_spans(1)
    harness.clock.advance(30)  # 不然这一段是空范围，关闭时会被删掉。
    disconnected_at = harness.clock.now
    harness.current.queue.put_nowait(Disconnected(harness.current.generation, disconnected_at))
    await harness.wait_status("reconnecting")
    await harness.add_attempts(10)
    harness.clock.advance(60)

    assert await harness.current.identify() is False
    row = await harness.wait_status("login_paused")

    [span] = await harness.spans()
    assert span.upper == disconnected_at  # pyright: ignore[reportAttributeAccessIssue]
    assert row.retry_at is not None
    assert _logged(captured, "collector.login_budget_exhausted")[0] == {
        "retry_at": row.retry_at.isoformat()
    }
    assert _logged(captured, "collector.identify_reserved") == [{"reason": "initial"}]


async def test_failures_log_only_kind_and_codes(  # covers: AC-11
    harness: Harness, captured: CapturingLogger
) -> None:
    row = await add_account(harness)
    state = SessionState(LoopState(generation=1), "reconnecting")
    reply: asyncio.Future[float | None] = asyncio.get_running_loop().create_future()

    await handle_event(
        harness.deps,
        row,
        state,
        RetryNeeded(1, ConnectionFailure("gateway_closed", close_code=4000), False, reply),
    )
    await handle_event(
        harness.deps, row, state, SessionEnded(1, ConnectionFailure("http", http_status=503))
    )

    assert _logged(captured, "collector.connection_failed") == [
        {"kind": "gateway_closed", "close_code": 4000, "http_status": None},
        {"kind": "http", "close_code": None, "http_status": 503},
    ]
    assert TOKEN not in str(captured.calls)


@pytest.mark.parametrize("change", ["token", "disable"])
async def test_waiting_client_writes_nothing_and_stops_on_config_change(  # covers: AC-2, AC-5
    harness: Harness, change: str
) -> None:
    """真的 HarvesterClient 在等待：状态不变、不写额度、不开 socket，改配置能立刻打断。"""
    wall = [1000.0]
    parked = asyncio.Event()

    async def park(_seconds: float) -> None:
        parked.set()
        await asyncio.Event().wait()

    guard = WakeGuard(lambda: wall[0], lambda: 50.0, park)
    guard.sample()
    wall[0] += 3600
    opened: list[object] = []
    closed = asyncio.Event()

    async def ws_factory(_client: object, **params: object) -> None:
        opened.append(params)

    class Client(HarvesterClient):
        async def close(self) -> None:
            await super().close()
            closed.set()

    def new_session(generation: int, queue: Queue, clock: Callable[[], datetime]) -> Client:
        client = Client(generation, queue, clock, ws_factory, guard=guard)

        async def login(_token: str) -> None:
            client._connection.user = SimpleNamespace(id=USER_ID, name="name")  # pyright: ignore

        client.login = login  # pyright: ignore[reportAttributeAccessIssue]
        return client

    harness.deps = replace(harness.deps, new_session=new_session)
    await add_account(harness)
    harness.start()
    await asyncio.wait_for(parked.wait(), timeout=5)

    row = await harness.read()
    assert row.status == "connecting"
    assert await harness.attempts() == 0
    assert opened == []

    if change == "token":
        cipher = encrypt(KEYS, PURPOSE_DISCORD_TOKEN, b"other")
        await harness.api(lambda s: accounts.replace_token(s, cipher, 1))
    else:
        await harness.api(lambda s: accounts.set_enabled(s, False))

    await asyncio.wait_for(closed.wait(), timeout=2)
    assert await harness.attempts() == 0
    assert opened == []


# spec 0008：消息事件。


class NotifyCounter:
    """在一个专用连接上 LISTEN 消息通道，记下收到的通知。"""

    def __init__(self) -> None:
        self.received: list[tuple[str, str]] = []

    def __call__(self, _conn: object, _pid: int, channel: str, payload: object) -> None:
        self.received.append((channel, str(payload)))


@pytest.fixture
async def notifications(engine: AsyncEngine) -> AsyncIterator[NotifyCounter]:
    counter = NotifyCounter()
    async with engine.connect() as conn:
        raw_conn = (await conn.get_raw_connection()).driver_connection
        assert raw_conn is not None
        await raw_conn.add_listener(NEW_MESSAGE_CHANNEL, counter)
        await raw_conn.add_listener(MESSAGE_CHANGED_CHANNEL, counter)
        yield counter
        await raw_conn.remove_listener(NEW_MESSAGE_CHANNEL, counter)
        await raw_conn.remove_listener(MESSAGE_CHANGED_CHANNEL, counter)


async def collecting_state(h: Harness) -> tuple[AccountRow, SessionState]:
    row = await add_account(h)
    await h.seed_collecting()
    async with h.factory() as session, session.begin():
        await accounts.record_status(session, row.id, 1, "online", None)
    row = await h.read()
    state = SessionState(LoopState(generation=1), "online")
    state = await supervisor._align(h.deps, row, state)  # pyright: ignore[reportPrivateUsage]
    return row, state


async def stored_count(h: Harness) -> int:
    async with h.factory() as session:
        return int(await session.scalar(select(func.count()).select_from(Message)) or 0)


def received(snapshot_id: int = MESSAGE_ID, **changes: object) -> MessageReceived:
    return MessageReceived(1, message(id=snapshot_id, **changes), at(0))


async def test_open_channels_match_the_database(harness: Harness) -> None:  # covers: AC-3
    row, state = await collecting_state(harness)

    assert state.open_channels == {CHANNEL.id}

    async with harness.factory() as session, session.begin():
        await session.execute(update(Channel).values(collect_enabled=False))
    state = await handle_event(harness.deps, row, state, supervisor.ChannelsWake())

    assert state.open_channels == frozenset()


async def test_failed_alignment_keeps_the_list(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-3
    row, state = await collecting_state(harness)

    async def boom(*_args: object) -> None:
        raise SQLAlchemyError

    monkeypatch.setattr(supervisor, "align_coverage", boom)
    with pytest.raises(SQLAlchemyError):
        await handle_event(harness.deps, row, state, supervisor.ChannelsWake())

    assert state.open_channels == {CHANNEL.id}


async def test_received_message_is_stored_and_notified(
    harness: Harness, notifications: NotifyCounter
) -> None:  # covers: AC-1, AC-15
    row, state = await collecting_state(harness)

    await handle_event(harness.deps, row, state, received())

    assert await stored_count(harness) == 1
    await asyncio.sleep(0.05)
    assert notifications.received == [
        (
            NEW_MESSAGE_CHANNEL,
            f'{{"channel_id": "{CHANNEL.id}", "message_id": "{MESSAGE_ID}"}}',
        )
    ]


async def test_closed_channel_never_touches_the_database(harness: Harness) -> None:
    # covers: AC-2
    row = await add_account(harness)

    def no_database() -> None:
        raise AssertionError("不应该连数据库")

    deps = replace(harness.deps, factory=no_database)  # pyright: ignore[reportArgumentType]
    state = SessionState(LoopState(generation=1), "online")

    for event in (
        received(),
        MessageEdited(1, message(), at(0)),
        MessagesDeleted(1, CHANNEL.id, (MESSAGE_ID,), at(0)),
    ):
        assert await handle_event(deps, row, state, event) == state


async def test_replay_is_stored_once_with_one_notification(
    harness: Harness, notifications: NotifyCounter
) -> None:  # covers: AC-5, AC-15
    row, state = await collecting_state(harness)

    await handle_event(harness.deps, row, state, received())
    await handle_event(harness.deps, row, state, received())

    assert await stored_count(harness) == 1
    await asyncio.sleep(0.05)
    assert [channel for channel, _ in notifications.received] == [NEW_MESSAGE_CHANNEL]


async def test_edit_notifications(harness: Harness, notifications: NotifyCounter) -> None:
    # covers: AC-4, AC-15
    row, state = await collecting_state(harness)

    # 编辑事件第一次入库：发 message_changed，不发 new_message。
    await handle_event(harness.deps, row, state, MessageEdited(1, message(), at(0)))
    # 内容没变也发。
    await handle_event(harness.deps, row, state, MessageEdited(1, message(), at(1)))
    await handle_event(
        harness.deps, row, state, MessageEdited(1, message(content="new", edited_at=at(2)), at(2))
    )

    await asyncio.sleep(0.05)
    assert [channel for channel, _ in notifications.received] == [MESSAGE_CHANGED_CHANNEL] * 3


async def test_delete_notifies_only_marked_ids(
    harness: Harness, notifications: NotifyCounter
) -> None:  # covers: AC-4, AC-15
    row, state = await collecting_state(harness)
    await handle_event(harness.deps, row, state, received())

    await handle_event(
        harness.deps, row, state, MessagesDeleted(1, CHANNEL.id, (MESSAGE_ID, 99), at(1))
    )
    await handle_event(harness.deps, row, state, MessagesDeleted(1, CHANNEL.id, (99,), at(1)))

    await asyncio.sleep(0.05)
    assert notifications.received[1:] == [
        (
            MESSAGE_CHANGED_CHANNEL,
            f'{{"channel_id": "{CHANNEL.id}", "message_ids": ["{MESSAGE_ID}"]}}',
        )
    ]


async def test_identify_after_disconnect_empties_the_list(harness: Harness) -> None:
    # covers: AC-5
    row, state = await collecting_state(harness)
    state = replace(state, loop=replace(state.loop, disconnected_at=harness.clock()))
    reply: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    next_state = await handle_event(harness.deps, row, state, IdentifyRequested(1, reply))

    assert next_state.open_channels == frozenset()
    assert reply.result() is True


async def test_transient_error_retries_then_succeeds(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-8
    row, state = await collecting_state(harness)
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    original = supervisor.ingest_message
    failures = [OperationalError("x", {}, Exception()), OSError()]

    async def flaky(*args: object) -> object:
        if failures:
            raise failures.pop()
        return await original(*args)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(supervisor, "ingest_message", flaky)
    deps = replace(harness.deps, sleep=record)

    await handle_event(deps, row, state, received())

    assert waits == [0.5, 1.0]
    assert await stored_count(harness) == 1


async def test_retries_exhausted_raises(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-8
    row, state = await collecting_state(harness)

    async def always(*_args: object) -> object:
        raise OSError

    monkeypatch.setattr(supervisor, "ingest_message", always)

    with pytest.raises(OSError):
        await handle_event(harness.deps, row, state, received())


async def test_bad_message_is_skipped_without_leaking_content(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:  # covers: AC-8, AC-24
    row, state = await collecting_state(harness)
    logger = CapturingLogger()
    monkeypatch.setattr(supervisor, "log", logger)

    # 正文里的 NUL 字符让 PostgreSQL 拒绝这条，后面的消息不受影响。
    await handle_event(harness.deps, row, state, received(content="secret\x00text"))
    await handle_event(harness.deps, row, state, received(MESSAGE_ID + 1))

    assert await stored_count(harness) == 1
    entry = next(c for c in logger.calls if c.args == ("collector.message_write_skipped",))
    assert entry.kwargs["message_id"] == MESSAGE_ID
    assert "secret" not in str(logger.calls)


def test_transient_classification() -> None:  # covers: AC-8
    assert supervisor.is_transient_db_error(OSError())
    assert supervisor.is_transient_db_error(OperationalError("x", {}, Exception()))
    assert not supervisor.is_transient_db_error(DataError("x", {}, Exception()))
    assert not supervisor.is_transient_db_error(ValueError())


async def test_session_end_writes_queued_messages_before_closing(harness: Harness) -> None:
    # covers: AC-7
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_spans(1)
    session = harness.current
    generation = session.generation
    stale = Disconnected(generation - 1, harness.clock())
    # 结束事件排在前面：循环退出时，后面两个还在队列里。
    session.queue.put_nowait(SessionEnded(generation))
    session.queue.put_nowait(stale)
    session.queue.put_nowait(MessageReceived(generation, message(), at(0)))

    async def stored() -> bool:
        return await stored_count(harness) == 1

    await harness.wait_until(stored)


async def test_drain_gives_up_when_database_hangs(harness: Harness) -> None:
    # covers: AC-7
    row, state = await collecting_state(harness)
    assert row is not None
    queue: Queue = asyncio.Queue()
    queue.put_nowait(received())
    other = supervisor.HeartbeatDue()
    queue.put_nowait(other)
    logger = CapturingLogger()

    @contextlib.asynccontextmanager
    async def hang() -> AsyncIterator[None]:
        await asyncio.sleep(10)
        yield

    deps = replace(harness.deps, factory=hang, drain_seconds=0.01)  # pyright: ignore[reportArgumentType]
    original = supervisor.log
    supervisor.log = logger  # pyright: ignore[reportAttributeAccessIssue]
    try:
        await supervisor._drain_messages(deps, state, queue)  # pyright: ignore[reportPrivateUsage]
    finally:
        supervisor.log = original

    assert queue.get_nowait() is other
    entry = next(c for c in logger.calls if c.args == ("collector.drain_failed",))
    assert entry.kwargs["dropped"] == 1


async def test_subscribes_only_wanted_guilds_once(harness: Harness) -> None:  # covers: AC-9, AC-11
    await add_account(harness)
    await harness.seed_collecting()
    harness.start()
    await harness.wait_spans(1)
    session = harness.current

    async def subscribed() -> bool:
        return session.subscribe_calls == [frozenset({GUILD.id})]

    await harness.wait_until(subscribed)
    harness.deps.channels_wake.set()
    await asyncio.sleep(0.05)
    assert session.subscribe_calls == [frozenset({GUILD.id})]


async def test_unsent_guild_is_retried_and_keeps_its_request(harness: Harness) -> None:
    # covers: AC-9, AC-12
    row, state = await collecting_state(harness)
    fake = FakeSession(1, asyncio.Queue(), USER_ID, [], [], unavailable=frozenset({GUILD.id}))
    state = replace(state, gateway=fake)
    async with harness.factory() as session, session.begin():
        await session.execute(update(Guild).values(thread_load_requested_at=at(0)))

    state = await handle_event(harness.deps, row, state, supervisor.ChannelsWake())
    assert state.subscribed == frozenset()
    async with harness.factory() as session:
        assert await session.scalar(select(Guild.thread_load_requested_at)) is not None

    fake.unavailable = frozenset()
    state = await handle_event(harness.deps, row, state, supervisor.ChannelsWake())
    assert state.subscribed == {GUILD.id}
    async with harness.factory() as session:
        assert await session.scalar(select(Guild.thread_load_requested_at)) is None


async def test_already_subscribed_guild_clears_request_without_a_command(
    harness: Harness,
) -> None:  # covers: AC-12
    row, state = await collecting_state(harness)
    fake = FakeSession(1, asyncio.Queue(), USER_ID, [], [])
    state = replace(state, gateway=fake, subscribed=frozenset({GUILD.id}))
    async with harness.factory() as session, session.begin():
        await session.execute(update(Guild).values(thread_load_requested_at=at(0)))

    await handle_event(harness.deps, row, state, supervisor.ChannelsWake())

    assert fake.subscribe_calls == []
    async with harness.factory() as session:
        assert await session.scalar(select(Guild.thread_load_requested_at)) is None


async def test_guild_sync_does_not_clear_the_request(harness: Harness) -> None:  # covers: AC-12
    row, state = await collecting_state(harness)
    async with harness.factory() as session, session.begin():
        await session.execute(update(Guild).values(thread_load_requested_at=at(0)))

    await handle_event(harness.deps, row, state, GuildChanged(1, GUILD_STATE))

    async with harness.factory() as session:
        assert await session.scalar(select(Guild.thread_load_requested_at)) is not None
