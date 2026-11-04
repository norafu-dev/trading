"""历史采集接口（spec 0012）。api 不连 Discord：只建任务、读进度，由 collector 去抓。"""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, StringConstraints

from harvester.api.errors import ApiError
from harvester.db import SessionFactoryDep
from harvester.db import history as history_store
from harvester.db.history import HistoryBatchRow, HistoryJobRow, InvalidRange, SkipReason
from harvester.db.history_ranges import progress
from harvester.db.snapshots import (
    HistoryError,
    HistoryJobStatus,
    HistoryOrigin,
    HistoryWaitReason,
    snowflake_time,
)

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承；写操作的 Origin 检查在中间件里。
history_router = APIRouter(prefix="/history", tags=["history"])

_BIGINT_MAX = 2**63 - 1
SnowflakeStr = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,19}$")]


class InvalidRangeError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_range",
            "起点要早于终点，时间要带时区",
        )


class TooManyChannelsError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "too_many_channels",
            f"每次最多选 {history_store.MAX_CHANNELS_PER_REQUEST} 个频道",
        )


class HistoryJobOut(BaseModel):
    id: str
    batch_id: str
    channel_id: str
    guild_name: str
    channel_name: str
    origin: HistoryOrigin
    refetch: bool
    range_start: datetime
    range_end: datetime
    status: HistoryJobStatus
    fetched_count: int
    pages_fetched: int
    cursor_at: datetime | None
    progress: float
    wait_reason: HistoryWaitReason | None
    wait_until: datetime | None
    not_before: datetime | None
    error: HistoryError | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class HistoryStatusCountsOut(BaseModel):
    pending: int
    running: int
    paused: int
    done: int
    failed: int
    cancelled: int


class HistoryBatchOut(BaseModel):
    batch_id: str
    channel_count: int
    fetched_count: int
    status_counts: HistoryStatusCountsOut


class HistoryJobPageOut(BaseModel):
    jobs: list[HistoryJobOut]
    batches: list[HistoryBatchOut]
    next_before_id: str | None


class SkippedChannelOut(BaseModel):
    channel_id: str
    reason: SkipReason


class CreateJobsOut(BaseModel):
    batch_id: str | None
    jobs: list[HistoryJobOut]
    skipped: list[SkippedChannelOut]


class CreateJobsIn(BaseModel):
    channel_ids: list[SnowflakeStr] = Field(min_length=1)
    # 为空表示全部历史（从频道创建时间起）。
    range_start: datetime | None = None
    # 为空表示现在；晚于现在时按现在算。
    range_end: datetime | None = None
    refetch: bool = False


def job_out(row: HistoryJobRow) -> HistoryJobOut:
    cursor_at = snowflake_time(row.cursor_message_id) if row.cursor_message_id is not None else None
    return HistoryJobOut(
        id=str(row.id),
        batch_id=str(row.batch_id),
        channel_id=str(row.channel_id),
        guild_name=row.guild_name,
        channel_name=row.channel_name,
        origin=row.origin,
        refetch=row.refetch,
        range_start=row.range_start,
        range_end=row.range_end,
        status=row.status,
        fetched_count=row.fetched_count,
        pages_fetched=row.pages_fetched,
        cursor_at=cursor_at,
        progress=progress(row.range_start, row.range_end, cursor_at, done=row.status == "done"),
        wait_reason=row.wait_reason,
        wait_until=row.wait_until,
        not_before=row.not_before,
        error=row.error,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _batch_out(row: HistoryBatchRow) -> HistoryBatchOut:
    return HistoryBatchOut(
        batch_id=str(row.batch_id),
        channel_count=row.channel_count,
        fetched_count=row.fetched_count,
        status_counts=HistoryStatusCountsOut(**row.status_counts),
    )


def _is_naive(value: datetime | None) -> bool:
    return value is not None and value.utcoffset() is None


@history_router.get("/jobs")
async def list_history_jobs(
    factory: SessionFactoryDep,
    view: Annotated[Literal["active", "finished"], Query()] = "active",
    since_days: Annotated[int, Query(ge=1, le=30)] = 7,
    before_id: Annotated[str | None, Query(pattern=r"^[0-9]{1,19}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> HistoryJobPageOut:
    before = int(before_id) if before_id is not None else None
    if before is not None and before > _BIGINT_MAX:
        before = _BIGINT_MAX
    async with factory() as session:
        page = await history_store.list_jobs(
            session, view, since_days=since_days, before_id=before, limit=limit
        )
    return HistoryJobPageOut(
        jobs=[job_out(j) for j in page.jobs],
        batches=[_batch_out(b) for b in page.batches],
        next_before_id=str(page.next_before_id) if page.next_before_id is not None else None,
    )


@history_router.post("/jobs")
async def create_history_jobs(body: CreateJobsIn, factory: SessionFactoryDep) -> CreateJobsOut:
    """建任务：去重、按频道创建时间截断起点、扣掉已覆盖和已排队的时间（AC-1、AC-2）。"""
    if len(body.channel_ids) > history_store.MAX_CHANNELS_PER_REQUEST:
        raise TooManyChannelsError
    if _is_naive(body.range_start) or _is_naive(body.range_end):
        raise InvalidRangeError
    # 超出 bigint 的 ID 一定不存在，直接按 not_found 跳过。
    ids = [int(i) for i in body.channel_ids if int(i) <= _BIGINT_MAX]
    oversize = [
        SkippedChannelOut(channel_id=i, reason="not_found")
        for i in dict.fromkeys(body.channel_ids)
        if int(i) > _BIGINT_MAX
    ]
    async with factory() as session, session.begin():
        result = await history_store.create_jobs(
            session,
            ids,
            body.range_start,
            body.range_end,
            refetch=body.refetch,
            origin="manual",
        )
        if isinstance(result, InvalidRange):
            raise InvalidRangeError
        rows = await history_store.get_jobs(session, result.job_ids)
    return CreateJobsOut(
        batch_id=str(result.batch_id) if result.batch_id is not None else None,
        jobs=[job_out(r) for r in rows],
        skipped=[
            *oversize,
            *(
                SkippedChannelOut(channel_id=str(s.channel_id), reason=s.reason)
                for s in result.skipped
            ),
        ],
    )
