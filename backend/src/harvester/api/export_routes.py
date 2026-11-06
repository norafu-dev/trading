"""频道消息导出接口（spec 0014）：预览和流式下载，只读库。

日志不记任何消息内容（AC-21）。
"""

import inspect
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Annotated, Literal

import anyio
import structlog
from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from harvester.api import export_format as fmt
from harvester.api.deps import ApiSettingsDep, ClockDep
from harvester.api.errors import ApiError
from harvester.api.message_routes import (
    ChannelNotFoundError,
    MessageNotFoundError,
    PathId,
)
from harvester.api.search_query import fields_payload
from harvester.db import SessionFactoryDep
from harvester.db import export as export_store
from harvester.db.export import ExportChannel, ExportRange
from harvester.db.history import channel_gaps
from harvester.db.search import is_query_canceled

# 由 routes.py 挂到 protected_router 上，登录检查从那里继承（AC-21）。
export_router = APIRouter(prefix="/channels", tags=["export"])

log = structlog.get_logger("harvester.export")


class InvalidExportError(ApiError):
    def __init__(self, error: fmt.ExportParamError) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_export",
            "导出参数不正确",
            fields=fields_payload(error.fields),
        )


class ExportTooLargeError(ApiError):
    def __init__(self) -> None:
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "export_too_large",
            "超过 10 万条上限，请缩小范围",
        )


class ExportEmptyError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_422_UNPROCESSABLE_CONTENT, "export_empty", "频道里没有消息")


class ExportTimeoutError(ApiError):
    def __init__(self) -> None:
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, "export_timeout", "统计超时")


class ExportPointOut(BaseModel):
    id: str
    created_at: datetime


class ExportGapOut(BaseModel):
    start: datetime
    end: datetime


class ExportPreviewOut(BaseModel):
    count: int
    over_limit: bool
    range_start: ExportPointOut | None
    range_end: ExportPointOut | None
    gaps: list[ExportGapOut]


Raw = Annotated[str | None, Query()]


def _point(point: export_store.Endpoint) -> ExportPointOut:
    return ExportPointOut(id=str(point.id), created_at=point.created_at)


async def _open_snapshot(
    stack: AsyncExitStack, factory: async_sessionmaker[AsyncSession]
) -> AsyncSession:
    """只读、REPEATABLE READ 的事务；快照在第一条查询时建立（AC-12、AC-20）。"""
    session = await stack.enter_async_context(factory())
    await stack.enter_async_context(session.begin())
    await session.connection(execution_options=dict(export_store.SNAPSHOT_OPTIONS))
    await export_store.apply_export_timeouts(session)
    return session


async def _channel_and_range(
    session: AsyncSession, channel_id: int, params: fmt.RangeParams
) -> tuple[ExportChannel, ExportRange | None]:
    channel = await export_store.get_export_channel(session, channel_id)
    if channel is None:
        raise ChannelNotFoundError
    resolved = await export_store.resolve_range(session, channel_id, params.start, params.end)
    if resolved == "message_not_found":
        raise MessageNotFoundError
    return channel, None if resolved == "empty" else resolved


@export_router.get("/{channel_id}/export/preview")
async def export_preview(
    channel_id: PathId,
    factory: SessionFactoryDep,
    clock: ClockDep,
    start: Raw = None,
    end: Raw = None,
) -> ExportPreviewOut:
    """下载前的条数和缺口（AC-6 到 AC-9）。

    超过上限时 `over_limit` 为真，频道没消息时 `count` 为 0。
    """
    params = fmt.parse_range(start, end)
    if isinstance(params, fmt.ExportParamError):
        raise InvalidExportError(params)
    cap = export_store.EXPORT_MAX_MESSAGES
    try:
        async with AsyncExitStack() as stack:
            session = await _open_snapshot(stack, factory)
            _channel, export_range = await _channel_and_range(session, int(channel_id), params)
            if export_range is None:
                return ExportPreviewOut(
                    count=0, over_limit=False, range_start=None, range_end=None, gaps=[]
                )
            count = await export_store.count_capped(session, int(channel_id), export_range, cap)
            gap_rows = await channel_gaps(session, [int(channel_id)])
    except DBAPIError as error:
        if is_query_canceled(error):
            raise ExportTimeoutError from None
        raise
    # 开放的终点算到现在；开放的起点已经落到频道最早的一条上（AC-8）。
    range_end_time = clock() if params.end is None else export_range.end.created_at
    gaps = fmt.clip_gaps(
        (gap for row in gap_rows for gap in row.gaps), export_range.start.created_at, range_end_time
    )
    return ExportPreviewOut(
        count=count,
        over_limit=count > cap,
        range_start=_point(export_range.start),
        range_end=_point(export_range.end),
        gaps=[ExportGapOut(start=lo, end=hi) for lo, hi in gaps],
    )


class _ClosingStreamingResponse(StreamingResponse):
    """不管怎么结束（写完、出错、浏览器断开，甚至第一个字节之前就断开），都关掉生成器和会话。

    Starlette 在 ASGI 2.4 下断开时抛 ClientDisconnect，会跳过 `background`，所以不用它（AC-19）。
    """

    def __init__(
        self,
        body: AsyncGenerator[bytes],
        on_close: Callable[[bool], Awaitable[None]],
        *,
        headers: dict[str, str],
        media_type: str,
    ) -> None:
        super().__init__(body, headers=headers, media_type=media_type)
        self._body = body
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            body = self._body
            never_started = inspect.getasyncgenstate(body) == inspect.AGEN_CREATED
            with anyio.CancelScope(shield=True):
                await body.aclose()
                await self._on_close(never_started)


def _log_outcome(
    outcome: Literal["export_finished", "export_failed", "export_aborted"],
    channel_id: int,
    export_format: fmt.ExportFormat,
    messages: int,
    size: int,
    started: float,
) -> None:
    method = log.warning if outcome != "export_finished" else log.info
    method(
        outcome,
        channel_id=str(channel_id),
        format=export_format,
        messages=messages,
        bytes=size,
        ms=round((time.monotonic() - started) * 1000),
    )


async def _body(
    session: AsyncSession,
    stack: AsyncExitStack,
    channel_id: int,
    export_range: ExportRange,
    params: fmt.ExportParams,
    site_origin: str,
    started: float,
) -> AsyncGenerator[bytes]:
    """键集分页，每批转成文本行发出去；内存里最多只有一批（AC-10）。"""
    messages = 0
    size = 0
    outcome: Literal["export_finished", "export_failed", "export_aborted"] = "export_aborted"
    try:
        if params.format == "csv":
            header = fmt.csv_header().encode()
            size += len(header)
            yield header
        after: int | None = None
        while True:
            batch = await export_store.fetch_batch(
                session,
                channel_id,
                export_range,
                after,
                with_revisions=params.format == "jsonl",
                batch_size=export_store.BATCH_SIZE,
            )
            if not batch:
                break
            if params.format == "csv":
                lines = (fmt.csv_row(m, params.tz, site_origin) for m in batch)
            else:
                lines = (fmt.jsonl_line(m, params.tz, site_origin) for m in batch)
            chunk = "".join(lines).encode()
            messages += len(batch)
            size += len(chunk)
            yield chunk
            after = batch[-1].id
        outcome = "export_finished"
    except Exception:
        # 文件已经开始写了，只能断开连接，不补任何结尾（AC-19）。
        outcome = "export_failed"
        raise
    finally:
        _log_outcome(outcome, channel_id, params.format, messages, size, started)
        with anyio.CancelScope(shield=True):
            await stack.aclose()


@export_router.get(
    "/{channel_id}/export",
    response_class=StreamingResponse,
    responses={200: {"description": "CSV 或 JSONL 文件，边查边写"}},
)
async def export_messages(
    channel_id: PathId,
    factory: SessionFactoryDep,
    settings: ApiSettingsDep,
    start: Raw = None,
    end: Raw = None,
    format: Raw = None,
    tz: Raw = None,
) -> StreamingResponse:
    """写文件之前的问题都返回 JSON 错误；之后的错误只能断开连接（AC-18、AC-19）。"""
    channel_int = int(channel_id)
    params = fmt.parse_export_params(start, end, format, tz)
    if isinstance(params, fmt.ExportParamError):
        raise InvalidExportError(params)

    started = time.monotonic()
    stack = AsyncExitStack()
    try:
        session = await _open_snapshot(stack, factory)
        channel, export_range = await _channel_and_range(session, channel_int, params.range)
        if export_range is None:
            raise ExportEmptyError
        cap = export_store.EXPORT_MAX_MESSAGES
        if await export_store.count_capped(session, channel_int, export_range, cap) > cap:
            raise ExportTooLargeError
    except DBAPIError as error:
        await stack.aclose()
        if is_query_canceled(error):
            raise ExportTimeoutError from None
        raise
    except BaseException:
        await stack.aclose()
        raise

    disposition = fmt.export_filename(
        channel.guild_name,
        channel.name,
        channel.id,
        export_range.start.created_at,
        export_range.end.created_at,
        params.tz,
        params.format,
    )
    body = _body(session, stack, channel_int, export_range, params, settings.site_origin, started)

    async def on_close(never_started: bool) -> None:
        if never_started:
            # 生成器没跑过，它的 finally 不会执行：日志和关会话在这里补上。
            _log_outcome("export_aborted", channel_int, params.format, 0, 0, started)
        await stack.aclose()

    return _ClosingStreamingResponse(
        body,
        on_close,
        headers={"Content-Disposition": disposition.header(), "Cache-Control": "no-store"},
        media_type=fmt.MEDIA_TYPES[params.format],
    )
