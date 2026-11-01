"""监督循环的集成测试：真实 PostgreSQL，假的 Discord 会话（spec 0005）。"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from harvester.collector.events import (
    Disconnected,
    IdentifyRequested,
    LoggedIn,
    Ready,
    Resumed,
    RetryNeeded,
    SessionEnded,
    SupervisorEvent,
)
from harvester.collector.failures import ConnectionFailure
from harvester.collector.supervisor import (
    Deps,
    GatewaySession,
    UnexpectedFailureError,
    run_supervisor,
)
from harvester.crypto import PURPOSE_DISCORD_TOKEN, encrypt
from harvester.db import account as accounts
from harvester.db.account import AccountRow
from harvester.db.models import Channel, ChannelCoverage, DiscordLoginAttempt
from tests.api.conftest import crypto_settings
from tests.db.builders import CHANNEL, at, seed_channel

Factory = async_sessionmaker[AsyncSession]
TOKEN = "fake-discord-token.abc123.XYZ"
USER_ID = 111
KEYS = crypto_settings().key_ring
Queue = asyncio.Queue[SupervisorEvent]


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
    closed: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)

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
        await self.finished.wait()

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

    async def attempts(self) -> int:
        async with self.factory() as session:
            count = await session.scalar(select(text("count(*)")).select_from(DiscordLoginAttempt))
        return int(count or 0)

    async def api(self, write: Callable[[AsyncSession], Awaitable[object]]) -> None:
        async with self.factory() as session, session.begin():
            await write(session)
        self.deps.wake.set()

    async def seed_collecting_channel(self) -> None:
        row = await self.read()
        async with self.factory() as session, session.begin():
            await seed_channel(session)
            await session.execute(
                update(Channel)
                .where(Channel.id == CHANNEL.id)
                .values(collector_account_id=row.id, collect_enabled=True)
            )

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


@pytest.fixture
async def harness(engine: AsyncEngine) -> AsyncIterator[Harness]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    clock = FakeClock()
    sessions: list[FakeSession] = []
    tokens: list[str] = []
    user_id = [USER_ID]

    def new_session(generation: int, queue: Queue, _clock: object) -> GatewaySession:
        session = FakeSession(generation, queue, user_id[0], tokens)
        sessions.append(session)
        return session

    deps = Deps(
        factory=factory,
        key_ring=KEYS,
        new_session=new_session,
        clock=clock,
        uniform=lambda _low, _high: 0.001,  # 退避压到几毫秒，测试不用真的等。
        wake=asyncio.Event(),
        heartbeat_interval=3600,
        crash_guard_seconds=0,
    )
    h = Harness(factory, clock, deps, sessions, tokens, user_id)
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
    await harness.seed_collecting_channel()
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
    row = await harness.read()
    async with harness.factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collector_account_id=row.id, collect_enabled=True)
        )
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
    await harness.wait_status("online")

    spans = await harness.spans()
    assert len(spans) == 2
    assert spans[0].upper == disconnect_at  # type: ignore[attr-defined]
    assert spans[1].upper is None and spans[1].lower == harness.clock()  # type: ignore[attr-defined]


async def test_disable_during_outage_closes_at_disconnect_time(
    harness: Harness,
) -> None:  # AC-6
    await add_account(harness)
    row = await harness.read()
    async with harness.factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collector_account_id=row.id, collect_enabled=True)
        )
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
    row = await harness.read()
    async with harness.factory() as session, session.begin():
        await seed_channel(session)
        await session.execute(
            update(Channel)
            .where(Channel.id == CHANNEL.id)
            .values(collector_account_id=row.id, collect_enabled=True)
        )
    harness.start()
    await harness.wait_status("online")
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
