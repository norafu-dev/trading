"""导出文件的生成（spec 0014）：纯函数，不碰数据库。"""

import csv
import io
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any, Literal, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from harvester.api.search_query import FieldError
from harvester.db.export import ExportAttachment, ExportMedia, ExportMessage
from harvester.db.search_text import strip_markup
from harvester.db.snapshots import EmbedData, ForwardedData

ExportFormat = Literal["csv", "jsonl"]

_BIGINT_MAX = 2**63 - 1
_NAME_MAX_CHARS = 30
# 两个名字各自最多 100 字节，整个文件名（加日期和扩展名）远低于 255 字节。
_NAME_MAX_BYTES = 100
_UNSAFE_NAME_CHARS = frozenset('/\\:*?"<>|')
_UNDERSCORES = re.compile(r"_+")
# Excel 会把这些字符开头的单元格当成公式（AC-15）。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "created_at",
    "author_id",
    "author_name",
    "content",
    "reply_to_id",
    "forwarded",
    "embeds",
    "attachments",
    "version",
    "edited_at",
    "deleted_at",
)
_CSV_TEXT_COLUMNS = frozenset({"author_name", "content", "forwarded", "embeds", "attachments"})

MEDIA_TYPES: dict[ExportFormat, str] = {
    "csv": "text/csv; charset=utf-8",
    "jsonl": "application/x-ndjson; charset=utf-8",
}


# --- 参数 ------------------------------------------------------------------


@dataclass(frozen=True)
class RangeParams:
    """两端的消息 ID，None 是开放的一端；两端都有时已经排好（start <= end）。"""

    start: int | None
    end: int | None


@dataclass(frozen=True)
class ExportParams:
    range: RangeParams
    format: ExportFormat
    tz: tzinfo
    tz_name: str


@dataclass(frozen=True)
class ExportParamError:
    fields: tuple[FieldError, ...]


def _field(name: str, kind: str) -> FieldError:
    return FieldError(("query", name), kind)


def _parse_id(raw: str | None, name: str) -> int | FieldError | None:
    if raw is None or raw == "":
        return None
    if not (raw.isascii() and raw.isdigit() and len(raw) <= 19 and 1 <= int(raw) <= _BIGINT_MAX):
        return _field(name, "invalid_id")
    return int(raw)


def parse_range(start: str | None, end: str | None) -> RangeParams | ExportParamError:
    """`start` 比 `end` 大时交换两端，不报错。"""
    parsed_start = _parse_id(start, "start")
    parsed_end = _parse_id(end, "end")
    errors = tuple(p for p in (parsed_start, parsed_end) if isinstance(p, FieldError))
    if errors:
        return ExportParamError(errors)
    assert not isinstance(parsed_start, FieldError) and not isinstance(parsed_end, FieldError)
    if parsed_start is not None and parsed_end is not None and parsed_start > parsed_end:
        parsed_start, parsed_end = parsed_end, parsed_start
    return RangeParams(parsed_start, parsed_end)


def parse_timezone(raw: str | None) -> tzinfo | None:
    """IANA 时区名；`../x` 这类名字 zoneinfo 抛 ValueError，也算非法（AC-18）。"""
    if raw is None or raw == "" or len(raw) > 64:
        return None
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def parse_export_params(
    start: str | None, end: str | None, format: str | None, tz: str | None
) -> ExportParams | ExportParamError:
    parsed_range = parse_range(start, end)
    errors: list[FieldError] = (
        list(parsed_range.fields) if isinstance(parsed_range, ExportParamError) else []
    )
    if format not in ("csv", "jsonl"):
        errors.append(_field("format", "invalid"))
    zone = parse_timezone(tz)
    if zone is None:
        errors.append(_field("tz", "invalid"))
    if errors or isinstance(parsed_range, ExportParamError):
        return ExportParamError(tuple(errors))
    assert zone is not None and tz is not None and format in ("csv", "jsonl")
    return ExportParams(parsed_range, format, zone, tz)


# --- 时间、链接、缺口 ---------------------------------------------------------


def format_time(moment: datetime, tz: tzinfo) -> str:
    """带偏移的 ISO 8601，精确到秒（AC-14）。"""
    return moment.astimezone(tz).replace(microsecond=0).isoformat()


def _optional_time(moment: datetime | None, tz: tzinfo) -> str | None:
    return format_time(moment, tz) if moment is not None else None


def media_link(media: ExportMedia | None, site_origin: str) -> str | None:
    """只有保存好的附件给链接，指向需要登录的 dashboard 接口（AC-17）。"""
    if media is None or media.status != "stored":
        return None
    return f"{site_origin}/api/media/{media.id}"


def clip_gaps(
    gaps: Iterable[tuple[datetime, datetime]], range_start: datetime, range_end: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    """缺口和范围取交集，丢掉没有交集的（AC-8）。"""
    clipped = ((max(lo, range_start), min(hi, range_end)) for lo, hi in gaps)
    return tuple((lo, hi) for lo, hi in clipped if lo < hi)


# --- 文件名 ----------------------------------------------------------------


@dataclass(frozen=True)
class ContentDisposition:
    filename: str
    ascii_filename: str

    def header(self) -> str:
        return (
            f'attachment; filename="{self.ascii_filename}"; '
            f"filename*=UTF-8''{quote(self.filename, safe='')}"
        )


def _is_unsafe(char: str) -> bool:
    return char in _UNSAFE_NAME_CHARS or char.isspace() or unicodedata.category(char) == "Cc"


def clean_name(raw: str, fallback: str) -> str:
    """非法字符、控制字符和空白换成 `_`，合并、去首尾，最多 30 个字符和 100 字节（AC-11）。"""
    replaced = "".join("_" if _is_unsafe(c) else c for c in raw)
    name = _UNDERSCORES.sub("_", replaced).strip("_")[:_NAME_MAX_CHARS]
    # Python 的字符串按码点切，不会切开代理对；超出字节数时逐个去掉末尾的字符。
    while len(name.encode()) > _NAME_MAX_BYTES:
        name = name[:-1]
    name = name.strip("_")
    return name or fallback


def export_filename(
    guild_name: str,
    channel_name: str,
    channel_id: int,
    first: datetime,
    last: datetime,
    tz: tzinfo,
    ext: ExportFormat,
) -> ContentDisposition:
    first_day = first.astimezone(tz).strftime("%Y-%m-%d")
    last_day = last.astimezone(tz).strftime("%Y-%m-%d")
    guild = clean_name(guild_name, "server")
    channel = clean_name(channel_name, "channel")
    return ContentDisposition(
        filename=f"{guild}_{channel}_{first_day}_{last_day}.{ext}",
        ascii_filename=f"channel-{channel_id}_{first_day}_{last_day}.{ext}",
    )


# --- embed、附件、转发 -------------------------------------------------------


def clean_embed(embed: EmbedData) -> EmbedData:
    """只对文字字段做标记处理；`timestamp` 和图片地址原样保留（AC-13、AC-14）。"""
    cleaned: dict[str, Any] = dict(embed)
    for key in ("title", "description", "footer_text"):
        if key in embed:
            cleaned[key] = strip_markup(cleaned[key])
    if "fields" in embed:
        cleaned["fields"] = [
            {**f, "name": strip_markup(f["name"]), "value": strip_markup(f["value"])}
            for f in embed["fields"]
        ]
    author = embed.get("author")
    if author is not None and "name" in author:
        cleaned["author"] = {**author, "name": strip_markup(author["name"])}
    return cast("EmbedData", cleaned)


def _embed_text(embed: EmbedData) -> str:
    author = embed.get("author")
    parts = [
        author.get("name", "") if author is not None else "",
        embed.get("title", ""),
        embed.get("description", ""),
        *(f"{f['name']}: {f['value']}" for f in embed.get("fields", ())),
        embed.get("footer_text", ""),
    ]
    return "\n".join(p for p in parts if p)


def embeds_text(embeds: Sequence[EmbedData]) -> str:
    texts = (_embed_text(clean_embed(e)) for e in embeds)
    return "\n\n".join(t for t in texts if t)


def _attachment_media(message: ExportMessage, attachment: ExportAttachment) -> ExportMedia | None:
    return next(
        (m for m in message.media if m.kind == "attachment" and m.attachment_id == attachment.id),
        None,
    )


def _forwarded_media(message: ExportMessage, slot: int) -> ExportMedia | None:
    return next(
        (m for m in message.media if m.kind == "forwarded_attachment" and m.slot == slot), None
    )


def _attachment_line(filename: str, link: str | None) -> str:
    return f"{filename} {link}" if link else filename


def attachments_text(message: ExportMessage, site_origin: str) -> str:
    """每个没被移除的附件一行：`文件名 链接`（AC-15、AC-17）。"""
    return "\n".join(
        _attachment_line(a.filename, media_link(_attachment_media(message, a), site_origin))
        for a in message.attachments
        if a.removed_at is None
    )


def forwarded_text(
    forwarded: ForwardedData | None, message: ExportMessage, site_origin: str
) -> str:
    if forwarded is None:
        return ""
    lines = [strip_markup(forwarded["content"])] if forwarded["content"] else []
    lines.extend(
        _attachment_line(name, media_link(_forwarded_media(message, slot), site_origin))
        for slot, name in enumerate(forwarded["attachment_names"])
    )
    return "\n".join(lines)


# --- CSV -------------------------------------------------------------------


def guard_formula(value: str) -> str:
    """防公式注入：可能被当成公式的单元格前面加 `'`（AC-15）。"""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def _csv_line(values: Sequence[str]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow(values)
    return buffer.getvalue()


def csv_header() -> str:
    """文件开头：UTF-8 BOM 加表头，Excel 双击打开中文不乱码（AC-15）。"""
    return "﻿" + _csv_line(CSV_COLUMNS)


def csv_row(message: ExportMessage, tz: tzinfo, site_origin: str) -> str:
    cells: dict[str, str] = {
        "id": str(message.id),
        "created_at": format_time(message.created_at, tz),
        "author_id": str(message.author_id),
        "author_name": message.author_name,
        "content": strip_markup(message.content),
        "reply_to_id": str(message.reply_to_id) if message.reply_to_id is not None else "",
        "forwarded": forwarded_text(message.forwarded, message, site_origin),
        "embeds": embeds_text(message.embeds),
        "attachments": attachments_text(message, site_origin),
        "version": str(message.version),
        "edited_at": _optional_time(message.edited_at, tz) or "",
        "deleted_at": _optional_time(message.deleted_at, tz) or "",
    }
    return _csv_line(
        [
            guard_formula(cells[column]) if column in _CSV_TEXT_COLUMNS else cells[column]
            for column in CSV_COLUMNS
        ]
    )


# --- JSONL -----------------------------------------------------------------


def _attachment_json(
    message: ExportMessage, attachment: ExportAttachment, tz: tzinfo, site_origin: str
) -> dict[str, Any]:
    media = _attachment_media(message, attachment)
    return {
        "id": str(attachment.id),
        "filename": attachment.filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "width": attachment.width,
        "height": attachment.height,
        "removed_at": _optional_time(attachment.removed_at, tz),
        "media_status": media.status if media is not None else None,
        "url": media_link(media, site_origin),
    }


def _forwarded_json(message: ExportMessage, site_origin: str) -> dict[str, Any] | None:
    forwarded = message.forwarded
    if forwarded is None:
        return None
    attachments: list[dict[str, Any]] = []
    for slot, name in enumerate(forwarded["attachment_names"]):
        media = _forwarded_media(message, slot)
        attachments.append(
            {
                "filename": name,
                "content_type": media.declared_content_type if media is not None else None,
                "size_bytes": media.declared_size_bytes if media is not None else None,
                "media_status": media.status if media is not None else None,
                "url": media_link(media, site_origin),
            }
        )
    guild_id = forwarded["guild_id"]
    return {
        "message_id": str(forwarded["message_id"]),
        "channel_id": str(forwarded["channel_id"]),
        "guild_id": str(guild_id) if guild_id is not None else None,
        "content": strip_markup(forwarded["content"]),
        "embeds": [clean_embed(e) for e in forwarded["embeds"]],
        "attachments": attachments,
    }


def jsonl_line(message: ExportMessage, tz: tzinfo, site_origin: str) -> str:
    """一行一个对象，中文不转义，ID 都是十进制字符串（AC-16）。"""
    record = {
        "id": str(message.id),
        "channel_id": str(message.channel_id),
        "guild_id": str(message.guild_id),
        "type": message.type,
        "created_at": format_time(message.created_at, tz),
        "author": {"id": str(message.author_id), "name": message.author_name},
        "content": strip_markup(message.content),
        "reply_to_id": str(message.reply_to_id) if message.reply_to_id is not None else None,
        "forwarded": _forwarded_json(message, site_origin),
        "embeds": [clean_embed(e) for e in message.embeds],
        "attachments": [_attachment_json(message, a, tz, site_origin) for a in message.attachments],
        "version": message.version,
        "edited_at": _optional_time(message.edited_at, tz),
        "deleted_at": _optional_time(message.deleted_at, tz),
        "revisions": [
            {
                "version": r.version,
                "content": strip_markup(r.content),
                "embeds": [clean_embed(e) for e in r.embeds],
                "replaced_at": format_time(r.replaced_at, tz),
            }
            for r in message.revisions
        ],
    }
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
