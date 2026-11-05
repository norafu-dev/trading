"""历史采集的数据库函数（spec 0012）。都接收显式传入的 `AsyncSession`，都不提交事务。

collector 对任务行的写入（`commit_page` 之外）都带 `status = 'running'` 条件并看影响行数：
api 刚改过状态时影响 0 行，collector 就放下这个任务，永远不会把你取消或暂停的任务改回去。
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, RowMapping, delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import Range, insert
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.db.history_ranges import (
    QUOTA_WINDOW,
    page_cursor,
    page_reached_end,
    partial_coverage_start,
    reconcile_interval,
    snowflake_floor,
)
from harvester.db.ingest import ingest_message, mark_messages_deleted
from harvester.db.models import (
    AccountChannelAccess,
    Channel,
    ChannelCoverage,
    CollectorHeartbeat,
    Guild,
    HistoryJob,
    HistoryPageHour,
    HistorySettings,
    Message,
)
from harvester.db.raw import HISTORY_CHANGED_CHANNEL
from harvester.db.snapshots import (
    COLLECTABLE_CHANNEL_TYPES,
    ChannelStatus,
    HistoryError,
    HistoryJobStatus,
    HistoryOrigin,
    HistoryPausedReason,
    HistoryWaitingReason,
    HistoryWaitReason,
    MessageSnapshot,
    snowflake_time,
)

# 每次提交最多这么多个频道（AC-1）。
MAX_CHANNELS_PER_REQUEST = 50
OPEN_STATUSES: tuple[HistoryJobStatus, ...] = ("pending", "running", "paused")
FINISHED_STATUSES: tuple[HistoryJobStatus, ...] = ("done", "failed", "cancelled")

SkipReason = Literal[
    "not_found", "channel_deleted", "not_collectable", "no_access", "nothing_to_fetch"
]


# ---- 通知 ---------------------------------------------------------------------------


async def notify_history_changed(
    session: AsyncSession, job_id: int | None = None, channel_id: int | None = None
) -> None:
    """任务状态、进度、设置或总开关变了。ID 用字符串，浏览器不会丢精度。"""
    payload: dict[str, str] = {}
    if job_id is not None:
        payload["job_id"] = str(job_id)
    if channel_id is not None:
        payload["channel_id"] = str(channel_id)
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": HISTORY_CHANGED_CHANNEL, "payload": json.dumps(payload)},
    )


# ---- 建任务 -------------------------------------------------------------------------


@dataclass(frozen=True)
class SkippedChannel:
    channel_id: int
    reason: SkipReason


@dataclass(frozen=True)
class CreateResult:
    batch_id: uuid.UUID | None
    job_ids: tuple[int, ...]
    skipped: tuple[SkippedChannel, ...]


@dataclass(frozen=True)
class InvalidRange:
    """起点不早于终点。"""


def _skip_reason(status: ChannelStatus, channel_type: int, can_read: bool) -> SkipReason | None:
    if status == "deleted":
        return "channel_deleted"
    if channel_type not in COLLECTABLE_CHANNEL_TYPES:
        return "not_collectable"
    if not can_read:
        return "no_access"
    return None


def _can_read_any():
    return exists(
        select(1).where(
            AccountChannelAccess.channel_id == Channel.id, AccountChannelAccess.can_read
        )
    ).label("can_read")


# 请求的时间段减去未完成任务，再（不整段重抓时）减去所有覆盖段，拆成连续段。
_REMAINING_SQL = text(
    """
    SELECT lower(r) AS range_start, upper(r) AS range_end
    FROM unnest(
        tstzmultirange(tstzrange(:range_start, :range_end, '[)'))
        - coalesce(
            (SELECT range_agg(tstzrange(range_start, range_end, '[)'))
             FROM history_job
             WHERE channel_id = :channel_id AND status IN ('pending', 'running', 'paused')),
            '{}'::tstzmultirange)
        - CASE WHEN :refetch THEN '{}'::tstzmultirange
               ELSE coalesce(
                   (SELECT range_agg(span) FROM channel_coverage WHERE channel_id = :channel_id),
                   '{}'::tstzmultirange)
          END
    ) AS r
    ORDER BY lower(r)
    """
)


async def _remaining_ranges(
    session: AsyncSession, channel_id: int, start: datetime, end: datetime, *, refetch: bool
) -> tuple[tuple[datetime, datetime], ...]:
    rows = await session.execute(
        _REMAINING_SQL,
        {"channel_id": channel_id, "range_start": start, "range_end": end, "refetch": refetch},
    )
    return tuple((row.range_start, row.range_end) for row in rows)


async def create_jobs(
    session: AsyncSession,
    channel_ids: Sequence[int],
    range_start: datetime | None,
    range_end: datetime | None,
    *,
    refetch: bool,
    origin: HistoryOrigin,
) -> CreateResult | InvalidRange:
    """`range_start` 为空表示全部历史（从频道创建时间起），`range_end` 为空或晚于现在时按现在算。

    先按 ID 顺序锁住频道行，两次并发提交不会排出重叠的任务，也不会互相等待（AC-2）。
    """
    db_now: datetime = await session.scalar(select(func.now()))  # pyright: ignore[reportAssignmentType]
    end = db_now if range_end is None or range_end > db_now else range_end
    if range_start is not None and range_start >= end:
        return InvalidRange()

    unique_ids = tuple(dict.fromkeys(channel_ids))
    rows = await session.execute(
        select(Channel.id, Channel.status, Channel.type, _can_read_any())
        .where(Channel.id.in_(unique_ids))
        .order_by(Channel.id)
        .with_for_update(of=Channel)
    )
    found = {row.id: row for row in rows}

    planned: list[tuple[int, datetime, datetime]] = []
    skipped: list[SkippedChannel] = []
    for channel_id in unique_ids:
        row = found.get(channel_id)
        if row is None:
            skipped.append(SkippedChannel(channel_id, "not_found"))
            continue
        reason = _skip_reason(row.status, row.type, row.can_read)
        if reason is not None:
            skipped.append(SkippedChannel(channel_id, reason))
            continue
        created = snowflake_time(channel_id)
        start = created if range_start is None or range_start < created else range_start
        pieces = (
            await _remaining_ranges(session, channel_id, start, end, refetch=refetch)
            if start < end
            else ()
        )
        if not pieces:
            skipped.append(SkippedChannel(channel_id, "nothing_to_fetch"))
            continue
        planned.extend((channel_id, lo, hi) for lo, hi in pieces)

    if not planned:
        return CreateResult(batch_id=None, job_ids=(), skipped=tuple(skipped))
    batch_id: uuid.UUID = await session.scalar(text("SELECT uuidv7()"))  # pyright: ignore[reportAssignmentType]
    job_ids = tuple(
        await session.scalars(
            insert(HistoryJob)
            .values(
                [
                    {
                        "batch_id": batch_id,
                        "channel_id": channel_id,
                        "origin": origin,
                        "refetch": refetch,
                        "range_start": lo,
                        "range_end": hi,
                    }
                    for channel_id, lo, hi in planned
                ]
            )
            .returning(HistoryJob.id)
        )
    )
    for job_id, (channel_id, _lo, _hi) in zip(job_ids, planned, strict=True):
        await notify_history_changed(session, job_id, channel_id)
    return CreateResult(batch_id=batch_id, job_ids=job_ids, skipped=tuple(skipped))


# ---- collector：领取、抓页、结束 -----------------------------------------------------


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    channel_id: int
    origin: HistoryOrigin
    refetch: bool
    range_start: datetime
    range_end: datetime
    cursor_message_id: int | None
    preempted_count: int


_CLAIM_SQL = text(
    """
    UPDATE history_job
    SET status = 'running', account_id = :account_id,
        started_at = coalesce(started_at, now()), not_before = NULL, updated_at = now()
    WHERE id = (
        SELECT id FROM history_job
        WHERE status = 'pending' AND (not_before IS NULL OR not_before <= now())
        ORDER BY (origin <> 'gap_auto'), id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, channel_id, origin, refetch, range_start, range_end, cursor_message_id,
              preempted_count
    """
)


async def claim_next_job(session: AsyncSession, account_id: int) -> ClaimedJob | None:
    """`gap_auto` 先，其余按 `id` 从小到大（AC-3）。"""
    row = (await session.execute(_CLAIM_SQL, {"account_id": account_id})).one_or_none()
    if row is None:
        return None
    await notify_history_changed(session, row.id, row.channel_id)
    return ClaimedJob(
        id=row.id,
        channel_id=row.channel_id,
        origin=row.origin,
        refetch=row.refetch,
        range_start=row.range_start,
        range_end=row.range_end,
        cursor_message_id=row.cursor_message_id,
        preempted_count=row.preempted_count,
    )


async def channel_readable(session: AsyncSession, account_id: int, channel_id: int) -> bool:
    """每一页请求之前重新读：频道已经不可读就不发请求（AC-3）。"""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    AccountChannelAccess.account_id == account_id,
                    AccountChannelAccess.channel_id == channel_id,
                    AccountChannelAccess.can_read,
                )
            )
        )
    )


async def has_stored_messages(
    session: AsyncSession, channel_id: int, range_start: datetime, range_end: datetime
) -> bool:
    """库里这个频道在 `[range_start, range_end)` 对应的 ID 区间内有没有没删除的消息（AC-3a）。"""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Message.channel_id == channel_id,
                    Message.id >= snowflake_floor(range_start),
                    Message.id < snowflake_floor(range_end),
                    Message.deleted_at.is_(None),
                )
            )
        )
    )


async def _add_history_coverage(
    session: AsyncSession, channel_id: int, start: datetime, end: datetime
) -> None:
    if start < end:
        session.add(
            ChannelCoverage(
                channel_id=channel_id, span=Range(start, end, bounds="[)"), source="history"
            )
        )
        await session.flush()


async def record_pages(session: AsyncSession, pages: int) -> None:
    """滚动 24 小时限额：按整点分桶累加（AC-5）。"""
    if pages <= 0:
        return
    stmt = insert(HistoryPageHour).values(hour=func.date_trunc("hour", func.now()), pages=pages)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[HistoryPageHour.hour],
            set_={"pages": HistoryPageHour.pages + stmt.excluded.pages},
        )
    )


async def _reconcile_page(
    session: AsyncSession, job: ClaimedJob, returned_ids: tuple[int, ...], raw_count: int
) -> None:
    """整段重抓：这一页的 ID 区间里，库里有、这一页没返回、还没删除的消息标成已删除（AC-13）。

    时间是这一页提交时的数据库时钟（`mark_messages_deleted` 的 `at`）。
    """
    before_id = job.cursor_message_id or snowflake_floor(job.range_end)
    low, high = reconcile_interval(before_id, returned_ids, raw_count, job.range_start)
    if low >= high:
        return
    stmt = select(Message.id).where(
        Message.channel_id == job.channel_id,
        Message.id >= low,
        Message.id < high,
        Message.deleted_at.is_(None),
    )
    if returned_ids:
        stmt = stmt.where(Message.id.not_in(returned_ids))
    missing = tuple(await session.scalars(stmt))
    if missing:
        at: datetime = await session.scalar(select(func.now()))  # pyright: ignore[reportAssignmentType]
        await mark_messages_deleted(session, missing, at, "reconciled")


@dataclass(frozen=True)
class PageCommitted:
    """`status` 是提交后任务行的状态；`cursor_message_id` 是新的断点。"""

    status: HistoryJobStatus
    cursor_message_id: int | None
    ingested: int


async def commit_page(
    session: AsyncSession,
    job: ClaimedJob,
    messages: Sequence[MessageSnapshot],
    returned_ids: tuple[int, ...],
    raw_count: int,
    request_count: int,
    received_at: datetime,
) -> PageCommitted:
    """一页的消息、断点、计数、限额计数在同一个事务里（调用方提交）。

    任务行已经是 `cancelled` 或 `failed` 时消息照样写（都是真实数据），但不动这一行。
    """
    status: HistoryJobStatus = await session.scalar(  # pyright: ignore[reportAssignmentType]
        select(HistoryJob.status).where(HistoryJob.id == job.id).with_for_update()
    )
    floor = snowflake_floor(job.range_start)
    ingested = 0
    for snap in sorted(messages, key=lambda m: m.id, reverse=True):
        if snap.id < floor:
            continue
        outcome = await ingest_message(session, snap, "history", received_at)
        if outcome not in ("skipped_type", "missing_channel"):
            ingested += 1
    await record_pages(session, request_count)

    if status in FINISHED_STATUSES:
        return PageCommitted(status=status, cursor_message_id=job.cursor_message_id, ingested=0)
    if job.refetch:
        await _reconcile_page(session, job, returned_ids, raw_count)

    new_cursor = page_cursor(returned_ids, job.range_start)
    if new_cursor is not None and job.cursor_message_id is not None:
        new_cursor = min(new_cursor, job.cursor_message_id)
    cursor = new_cursor if new_cursor is not None else job.cursor_message_id
    finish = status == "running" and page_reached_end(returned_ids, raw_count, job.range_start)
    values: dict[str, object] = {
        "cursor_message_id": cursor,
        "fetched_count": HistoryJob.fetched_count + ingested,
        "pages_fetched": HistoryJob.pages_fetched + 1,
        "preempted_count": 0,
        "updated_at": func.now(),
    }
    if finish:
        values |= {
            "status": "done",
            "finished_at": func.now(),
            "wait_reason": None,
            "wait_until": None,
        }
    await session.execute(update(HistoryJob).where(HistoryJob.id == job.id).values(values))
    if finish:
        await _add_history_coverage(session, job.channel_id, job.range_start, job.range_end)
    await notify_history_changed(session, job.id, job.channel_id)
    return PageCommitted(
        status="done" if finish else status, cursor_message_id=cursor, ingested=ingested
    )


async def fail_job(session: AsyncSession, job_id: int, error: HistoryError) -> bool:
    """`running` → `failed`。有断点时写一段部分覆盖，`suspect_empty` 除外（AC-11、AC-3a）。

    返回 False 表示这一行已经不是 `running`（api 刚改过），collector 不再碰它。
    """
    row = (
        await session.execute(
            update(HistoryJob)
            .where(HistoryJob.id == job_id, HistoryJob.status == "running")
            .values(
                status="failed",
                error=error,
                finished_at=func.now(),
                wait_reason=None,
                wait_until=None,
                updated_at=func.now(),
            )
            .returning(HistoryJob.channel_id, HistoryJob.cursor_message_id, HistoryJob.range_end)
        )
    ).one_or_none()
    if row is None:
        return False
    if error != "suspect_empty" and row.cursor_message_id is not None:
        await _add_history_coverage(
            session, row.channel_id, partial_coverage_start(row.cursor_message_id), row.range_end
        )
    await notify_history_changed(session, job_id, row.channel_id)
    return True


async def release_job(
    session: AsyncSession,
    job_id: int,
    cool_down: timedelta | None = None,
    *,
    preempted: bool = False,
) -> bool:
    """`running` → `pending`，断点保留（AC-10）。返回 False 表示这一行已经不是 `running`。

    `cool_down` 给了时写 `not_before = now() + cool_down`，用数据库时钟，和领取时比较的是同一个。
    `preempted` 为真表示让位给自动补缺口，`preempted_count` 加 1。
    """
    channel_id = await session.scalar(
        update(HistoryJob)
        .where(HistoryJob.id == job_id, HistoryJob.status == "running")
        .values(
            status="pending",
            wait_reason=None,
            wait_until=None,
            not_before=None if cool_down is None else func.now() + cool_down,
            preempted_count=HistoryJob.preempted_count + 1
            if preempted
            else HistoryJob.preempted_count,
            updated_at=func.now(),
        )
        .returning(HistoryJob.channel_id)
    )
    if channel_id is None:
        return False
    await notify_history_changed(session, job_id, channel_id)
    return True


async def set_job_wait(
    session: AsyncSession, job_id: int, reason: HistoryWaitReason | None, until: datetime | None
) -> bool:
    """单个任务因为限速或重试在等（AC-6）；返回 False 表示这一行已经不是 `running`。"""
    channel_id = await session.scalar(
        update(HistoryJob)
        .where(HistoryJob.id == job_id, HistoryJob.status == "running")
        .values(wait_reason=reason, wait_until=until, updated_at=func.now())
        .returning(HistoryJob.channel_id)
    )
    if channel_id is None:
        return False
    await notify_history_changed(session, job_id, channel_id)
    return True


async def reset_running_on_start(session: AsyncSession) -> int:
    """collector 启动时：所有 `running` 改回 `pending`，清空等待字段和整体等待原因。"""
    released = len(
        (
            await session.scalars(
                update(HistoryJob)
                .where(HistoryJob.status == "running")
                .values(status="pending", wait_reason=None, wait_until=None, updated_at=func.now())
                .returning(HistoryJob.id)
            )
        ).all()
    )
    await session.execute(
        update(CollectorHeartbeat)
        .where(CollectorHeartbeat.id)
        .values(history_waiting_reason=None, history_waiting_until=None)
    )
    if released:
        await notify_history_changed(session)
    return released


# ---- 任务操作（api 侧，AC-9、AC-11） -------------------------------------------------

JobAction = Literal["pause", "resume", "cancel", "retry"]
BatchAction = Literal["pause", "resume", "cancel"]

# 每个操作允许从哪些状态出发，以及目标状态。
_TRANSITIONS: Mapping[JobAction, tuple[tuple[HistoryJobStatus, ...], HistoryJobStatus]] = {
    "pause": (("pending", "running"), "paused"),
    "resume": (("paused",), "pending"),
    "cancel": (("pending", "running", "paused", "failed"), "cancelled"),
    "retry": (("failed",), "pending"),
}


@dataclass(frozen=True)
class JobNotFound:
    pass


@dataclass(frozen=True)
class BatchNotFound:
    pass


@dataclass(frozen=True)
class InvalidTransition:
    """任务当前的状态不能做这个操作。"""

    current: HistoryJobStatus


@dataclass(frozen=True)
class Transitioned:
    job_id: int


def _transition_values(action: JobAction) -> dict[str, object]:
    target = _TRANSITIONS[action][1]
    values: dict[str, object] = {
        "status": target,
        "wait_reason": None,
        "wait_until": None,
        "updated_at": func.now(),
    }
    if action == "cancel":
        # 失败的任务被取消时，`error` 只允许出现在 `failed` 上，要一起清掉。
        values |= {"finished_at": func.now(), "error": None}
    elif action == "retry":
        values |= {"error": None, "finished_at": None}
    return values


async def _apply_transition(
    session: AsyncSession, rows: Sequence[RowMapping], action: JobAction
) -> int:
    """调用方已经 `FOR UPDATE` 锁住了这些行，并确认状态都符合。

    取消一个有断点、还没结束的任务时，在同一个事务里写部分覆盖段（AC-11）；
    `failed` 变成 `cancelled` 失败时已经写过，不再写。
    """
    if not rows:
        return 0
    values = _transition_values(action)
    await session.execute(
        update(HistoryJob).where(HistoryJob.id.in_([r["id"] for r in rows])).values(values)
    )
    for row in rows:
        if (
            action == "cancel"
            and row["status"] != "failed"
            and row["cursor_message_id"] is not None
        ):
            await _add_history_coverage(
                session,
                row["channel_id"],
                partial_coverage_start(row["cursor_message_id"]),
                row["range_end"],
            )
        await notify_history_changed(session, row["id"], row["channel_id"])
    return len(rows)


def _lock_rows(*where: ColumnElement[bool]):
    return (
        select(
            HistoryJob.id,
            HistoryJob.channel_id,
            HistoryJob.status,
            HistoryJob.cursor_message_id,
            HistoryJob.range_end,
        )
        .where(*where)
        .order_by(HistoryJob.id)
        .with_for_update()
    )


async def transition_job(
    session: AsyncSession, job_id: int, action: JobAction
) -> Transitioned | JobNotFound | InvalidTransition:
    """单个任务的暂停、继续、取消、重试；先锁住行，和 collector 的写入互相串行。"""
    row = (await session.execute(_lock_rows(HistoryJob.id == job_id))).mappings().one_or_none()
    if row is None:
        return JobNotFound()
    if row["status"] not in _TRANSITIONS[action][0]:
        return InvalidTransition(row["status"])
    await _apply_transition(session, [row], action)
    return Transitioned(job_id)


async def transition_batch(
    session: AsyncSession, batch_id: uuid.UUID, action: BatchAction
) -> int | BatchNotFound:
    """整批操作只作用于状态符合的行，返回实际改了几行；整批加锁按 `id` 排序（AC-9）。"""
    exists_ = await session.scalar(select(exists().where(HistoryJob.batch_id == batch_id)))
    if not exists_:
        return BatchNotFound()
    rows = (
        (
            await session.execute(
                _lock_rows(
                    HistoryJob.batch_id == batch_id,
                    HistoryJob.status.in_(_TRANSITIONS[action][0]),
                )
            )
        )
        .mappings()
        .all()
    )
    return await _apply_transition(session, rows, action)


# ---- 清理（AC-21） ------------------------------------------------------------------

JOB_RETENTION = timedelta(days=30)
PAGE_HOUR_RETENTION = timedelta(hours=48)


@dataclass(frozen=True)
class CleanupResult:
    jobs: int
    page_hours: int


async def cleanup_history(session: AsyncSession) -> CleanupResult:
    """删除结束 30 天以上的任务行和 48 小时以前的限额计数行；覆盖段和消息不删。"""
    jobs = await session.scalars(
        delete(HistoryJob)
        .where(
            HistoryJob.status.in_(FINISHED_STATUSES),
            HistoryJob.finished_at < func.now() - JOB_RETENTION,
        )
        .returning(HistoryJob.id)
    )
    hours = await session.scalars(
        delete(HistoryPageHour)
        .where(HistoryPageHour.hour < func.now() - PAGE_HOUR_RETENTION)
        .returning(HistoryPageHour.hour)
    )
    return CleanupResult(jobs=len(jobs.all()), page_hours=len(hours.all()))


async def job_is_running(session: AsyncSession, job_id: int) -> bool:
    """每页请求之前再看一眼：api 暂停或取消之后不再发请求（AC-9）。"""
    return bool(
        await session.scalar(
            select(exists().where(HistoryJob.id == job_id, HistoryJob.status == "running"))
        )
    )


# ---- 设置和总开关 -------------------------------------------------------------------


@dataclass(frozen=True)
class HistorySettingsRow:
    page_delay_min_s: int
    page_delay_max_s: int
    daily_page_limit: int
    run_minutes: int
    rest_minutes: int
    auto_gap_max_hours: int
    paused_reason: HistoryPausedReason | None
    paused_at: datetime | None


async def get_history_settings(session: AsyncSession) -> HistorySettingsRow:
    row = (
        await session.execute(select(HistorySettings).where(HistorySettings.id == 1))
    ).scalar_one()
    return HistorySettingsRow(
        page_delay_min_s=row.page_delay_min_s,
        page_delay_max_s=row.page_delay_max_s,
        daily_page_limit=row.daily_page_limit,
        run_minutes=row.run_minutes,
        rest_minutes=row.rest_minutes,
        auto_gap_max_hours=row.auto_gap_max_hours,
        paused_reason=row.paused_reason,
        paused_at=row.paused_at,
    )


async def set_history_paused(session: AsyncSession, reason: HistoryPausedReason) -> None:
    """自动暂停（限速、账号风险）或手动暂停；已经有原因时不覆盖（AC-7、AC-8）。"""
    await session.execute(
        update(HistorySettings)
        .where(HistorySettings.id == 1, HistorySettings.paused_reason.is_(None))
        .values(paused_reason=reason, paused_at=func.now(), updated_at=func.now())
    )
    await notify_history_changed(session)


async def resume_history(session: AsyncSession) -> None:
    """「继续」：清空暂停原因和时间，并唤醒 collector（AC-8）。"""
    await session.execute(
        update(HistorySettings)
        .where(HistorySettings.id == 1)
        .values(paused_reason=None, paused_at=None, updated_at=func.now())
    )
    await notify_history_changed(session)


async def update_history_settings(
    session: AsyncSession,
    *,
    page_delay_min_s: int,
    page_delay_max_s: int,
    daily_page_limit: int,
    run_minutes: int,
    rest_minutes: int,
    auto_gap_max_hours: int,
) -> HistorySettingsRow:
    """六个数值一起保存；范围检查在接口层，数据库约束兜底（AC-19）。"""
    await session.execute(
        update(HistorySettings)
        .where(HistorySettings.id == 1)
        .values(
            page_delay_min_s=page_delay_min_s,
            page_delay_max_s=page_delay_max_s,
            daily_page_limit=daily_page_limit,
            run_minutes=run_minutes,
            rest_minutes=rest_minutes,
            auto_gap_max_hours=auto_gap_max_hours,
            updated_at=func.now(),
        )
    )
    await notify_history_changed(session)
    return await get_history_settings(session)


async def pages_in_window(session: AsyncSession, now: datetime) -> tuple[tuple[datetime, int], ...]:
    """滚动 24 小时窗口里的限额桶，按小时从早到晚（AC-5）。"""
    rows = await session.execute(
        select(HistoryPageHour.hour, HistoryPageHour.pages)
        .where(HistoryPageHour.hour > now - QUOTA_WINDOW)
        .order_by(HistoryPageHour.hour)
    )
    return tuple((hour, pages) for hour, pages in rows.all())


async def has_claimable_gap_auto(session: AsyncSession) -> bool:
    """有没有可以领取的 `gap_auto` 任务：用来判断要不要让位（AC-10）。"""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    HistoryJob.status == "pending",
                    HistoryJob.origin == "gap_auto",
                    or_(HistoryJob.not_before.is_(None), HistoryJob.not_before <= func.now()),
                )
            )
        )
    )


async def set_history_waiting(
    session: AsyncSession, reason: HistoryWaitingReason | None, until: datetime | None
) -> None:
    """历史采集整体休息或等限额时写入，结束时清空（AC-6）。"""
    await session.execute(
        update(CollectorHeartbeat)
        .where(CollectorHeartbeat.id)
        .values(history_waiting_reason=reason, history_waiting_until=until)
    )
    await notify_history_changed(session)


async def record_history_heartbeat(session: AsyncSession) -> None:
    """历史任务心跳；进程心跳那一行还没建出来时什么也不写（AC-20）。"""
    await session.execute(
        update(CollectorHeartbeat)
        .where(CollectorHeartbeat.id)
        .values(history_worker_seen_at=func.now())
    )


# ---- 列表 ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryJobRow:
    id: int
    batch_id: uuid.UUID
    channel_id: int
    guild_name: str
    channel_name: str
    origin: HistoryOrigin
    refetch: bool
    range_start: datetime
    range_end: datetime
    status: HistoryJobStatus
    fetched_count: int
    pages_fetched: int
    cursor_message_id: int | None
    wait_reason: HistoryWaitReason | None
    wait_until: datetime | None
    not_before: datetime | None
    error: HistoryError | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class HistoryBatchRow:
    batch_id: uuid.UUID
    channel_count: int
    fetched_count: int
    status_counts: Mapping[HistoryJobStatus, int]


@dataclass(frozen=True)
class HistoryJobPage:
    jobs: tuple[HistoryJobRow, ...]
    batches: tuple[HistoryBatchRow, ...]
    next_before_id: int | None


def _job_select():
    return (
        select(
            HistoryJob.id,
            HistoryJob.batch_id,
            HistoryJob.channel_id,
            Guild.name.label("guild_name"),
            Channel.name.label("channel_name"),
            HistoryJob.origin,
            HistoryJob.refetch,
            HistoryJob.range_start,
            HistoryJob.range_end,
            HistoryJob.status,
            HistoryJob.fetched_count,
            HistoryJob.pages_fetched,
            HistoryJob.cursor_message_id,
            HistoryJob.wait_reason,
            HistoryJob.wait_until,
            HistoryJob.not_before,
            HistoryJob.error,
            HistoryJob.created_at,
            HistoryJob.started_at,
            HistoryJob.finished_at,
        )
        .join(Channel, Channel.id == HistoryJob.channel_id)
        .join(Guild, Guild.id == Channel.guild_id)
    )


def _to_job_row(row: RowMapping) -> HistoryJobRow:
    return HistoryJobRow(**row)


async def _batch_rows(
    session: AsyncSession, batch_ids: Sequence[uuid.UUID]
) -> tuple[HistoryBatchRow, ...]:
    """批次合计按整个批次在数据库里算，不受分页影响（AC-14）。"""
    if not batch_ids:
        return ()
    counts = [
        func.count().filter(HistoryJob.status == s).label(s)
        for s in ("pending", "running", "paused", "done", "failed", "cancelled")
    ]
    rows = await session.execute(
        select(
            HistoryJob.batch_id,
            func.count(HistoryJob.channel_id.distinct()).label("channel_count"),
            func.coalesce(func.sum(HistoryJob.fetched_count), 0).label("fetched_count"),
            *counts,
        )
        .where(HistoryJob.batch_id.in_(batch_ids))
        .group_by(HistoryJob.batch_id)
    )
    by_id = {
        row.batch_id: HistoryBatchRow(
            batch_id=row.batch_id,
            channel_count=row.channel_count,
            fetched_count=int(row.fetched_count),
            status_counts={
                "pending": row.pending,
                "running": row.running,
                "paused": row.paused,
                "done": row.done,
                "failed": row.failed,
                "cancelled": row.cancelled,
            },
        )
        for row in rows
    }
    return tuple(by_id[b] for b in batch_ids if b in by_id)


async def list_jobs(
    session: AsyncSession,
    view: Literal["active", "finished"],
    *,
    since_days: int = 7,
    before_id: int | None = None,
    limit: int = 50,
) -> HistoryJobPage:
    """`active`：所有未结束和失败的行，不分页。

    `finished`：最近 N 天结束的 `done` 和 `cancelled`，按结束时间倒序分页。
    """
    if view == "active":
        stmt = (
            _job_select()
            .where(HistoryJob.status.in_((*OPEN_STATUSES, "failed")))
            .order_by(HistoryJob.batch_id, HistoryJob.id)
        )
        jobs = tuple(_to_job_row(r) for r in (await session.execute(stmt)).mappings())
        next_before_id = None
    else:
        stmt = (
            _job_select()
            .where(
                HistoryJob.status.in_(("done", "cancelled")),
                HistoryJob.finished_at > func.now() - func.make_interval(0, 0, 0, since_days),
            )
            .order_by(HistoryJob.finished_at.desc(), HistoryJob.id.desc())
        )
        if before_id is not None:
            anchor = (
                select(HistoryJob.finished_at).where(HistoryJob.id == before_id).scalar_subquery()
            )
            stmt = stmt.where(
                (HistoryJob.finished_at < anchor)
                | ((HistoryJob.finished_at == anchor) & (HistoryJob.id < before_id))
            )
        rows = tuple(
            _to_job_row(r) for r in (await session.execute(stmt.limit(limit + 1))).mappings()
        )
        jobs = rows[:limit]
        next_before_id = jobs[-1].id if len(rows) > limit else None
    batch_ids = tuple(dict.fromkeys(j.batch_id for j in jobs))
    return HistoryJobPage(
        jobs=jobs, batches=await _batch_rows(session, batch_ids), next_before_id=next_before_id
    )


async def get_job(session: AsyncSession, job_id: int) -> HistoryJobRow | None:
    row = (
        (await session.execute(_job_select().where(HistoryJob.id == job_id)))
        .mappings()
        .one_or_none()
    )
    return _to_job_row(row) if row is not None else None


async def get_jobs(session: AsyncSession, job_ids: Sequence[int]) -> tuple[HistoryJobRow, ...]:
    if not job_ids:
        return ()
    stmt = _job_select().where(HistoryJob.id.in_(job_ids)).order_by(HistoryJob.id)
    return tuple(_to_job_row(r) for r in (await session.execute(stmt)).mappings())
