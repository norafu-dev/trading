"""spec 0014：导出文件生成的纯函数（不连数据库）。"""

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from harvester.api import export_format as fmt
from harvester.db.export import ExportAttachment, ExportMedia, ExportMessage, ExportRevision
from harvester.db.search_text import build_search_text, clean_text, strip_markup

SITE = "https://harvester.example"
SHANGHAI = ZoneInfo("Asia/Shanghai")
T = datetime(2026, 11, 6, 7, 30, 0, 123456, tzinfo=UTC)


def msg(**changes: Any) -> ExportMessage:
    base: dict[str, Any] = {
        "id": 1300000000000000001,
        "channel_id": 201,
        "guild_id": 100,
        "author_id": 300,
        "author_name": "KOL",
        "type": 0,
        "content": "hello",
        "embeds": (),
        "forwarded": None,
        "reply_to_id": None,
        "version": 1,
        "created_at": T,
        "edited_at": None,
        "deleted_at": None,
    }
    return ExportMessage(**{**base, **changes})


def att(id: int, removed: bool = False) -> ExportAttachment:
    return ExportAttachment(
        id=id,
        filename=f"f{id}.png",
        content_type="image/png",
        size_bytes=10,
        width=1,
        height=2,
        removed_at=T if removed else None,
    )


def media(id: int, status: str, *, attachment_id: int | None = None, slot: int | None = None):
    return ExportMedia(
        id=id,
        kind="attachment" if attachment_id is not None else "forwarded_attachment",
        attachment_id=attachment_id,
        slot=slot,
        status=status,  # type: ignore[arg-type]
        declared_content_type="image/jpeg",
        declared_size_bytes=99,
    )


def embed_media(id: int, kind: str, slot: int, status: str = "stored") -> ExportMedia:
    return ExportMedia(
        id=id,
        kind=kind,  # type: ignore[arg-type]
        attachment_id=None,
        slot=slot,
        status=status,  # type: ignore[arg-type]
        declared_content_type=None,
        declared_size_bytes=None,
    )


def parse_csv_row(line: str) -> dict[str, str]:
    values = next(csv.reader(io.StringIO(line)))
    return dict(zip(fmt.CSV_COLUMNS, values, strict=True))


# --- strip_markup（AC-13、AC-22） -------------------------------------------


def test_strip_markup_keeps_case_and_whitespace() -> None:
    raw = "Hi <@123>  <@!4> <@&5> <#6> <t:1700000000:R>\n<:Pepe:7> <a:Dance:8> </Cmd Sub:9>"
    assert strip_markup(raw) == "Hi" + " " * 6 + "\n:Pepe: :Dance: /Cmd Sub"


def test_clean_text_unchanged_after_split() -> None:
    raw = "  BTC <@1>  Long\n<:pepe:2> </ask:3>  "
    assert clean_text(raw) == "btc long :pepe: /ask"
    assert build_search_text(raw, None, None, ()) == "btc long :pepe: /ask"


# --- 参数（AC-18） ----------------------------------------------------------


def test_parse_range_swaps_and_accepts_open_ends() -> None:
    assert fmt.parse_range("9", "3") == fmt.RangeParams(3, 9)
    assert fmt.parse_range(None, "") == fmt.RangeParams(None, None)
    error = fmt.parse_range("abc", "0")
    assert isinstance(error, fmt.ExportParamError)
    assert [f.loc for f in error.fields] == [("query", "start"), ("query", "end")]


@pytest.mark.parametrize("tz", ["../x", "Not/AZone", "", None, "/etc/passwd", "x" * 100])
def test_invalid_timezone(tz: str | None) -> None:
    result = fmt.parse_export_params("1", "2", "csv", tz)
    assert isinstance(result, fmt.ExportParamError)
    assert [f.loc for f in result.fields] == [("query", "tz")]


def test_parse_export_params_collects_all_fields() -> None:
    result = fmt.parse_export_params("x", None, "xlsx", "Asia/Shanghai")
    assert isinstance(result, fmt.ExportParamError)
    assert {f.loc[1] for f in result.fields} == {"start", "format"}
    ok = fmt.parse_export_params(None, "5", "jsonl", "Asia/Shanghai")
    assert isinstance(ok, fmt.ExportParams)
    assert ok.format == "jsonl" and ok.range == fmt.RangeParams(None, 5)


# --- 时间、链接、缺口（AC-8、AC-14、AC-17） -------------------------------------


def test_format_time_offset_and_seconds() -> None:
    assert fmt.format_time(T, SHANGHAI) == "2026-11-06T15:30:00+08:00"
    assert fmt.format_time(T, UTC) == "2026-11-06T07:30:00+00:00"


def test_media_link_only_when_stored() -> None:
    assert fmt.media_link(media(42, "stored", attachment_id=1), SITE) == f"{SITE}/api/media/42"
    for status in ("pending", "too_large", "failed", "gone", "no_space"):
        assert fmt.media_link(media(42, status, attachment_id=1), SITE) is None
    assert fmt.media_link(None, SITE) is None


def test_clip_gaps() -> None:
    lo = T
    hi = T + timedelta(hours=10)
    gaps = [
        (lo - timedelta(hours=2), lo + timedelta(hours=1)),  # 部分重叠
        (lo + timedelta(hours=3), lo + timedelta(hours=4)),  # 在里面
        (hi + timedelta(hours=1), hi + timedelta(hours=2)),  # 不重叠
        (lo - timedelta(hours=5), lo),  # 只碰到边
    ]
    assert fmt.clip_gaps(gaps, lo, hi) == (
        (lo, lo + timedelta(hours=1)),
        (lo + timedelta(hours=3), lo + timedelta(hours=4)),
    )


# --- 文件名（AC-11） --------------------------------------------------------


def test_filename_cleans_names_and_uses_tz_dates() -> None:
    late = datetime(2026, 11, 6, 20, 0, tzinfo=UTC)  # 上海已经是 11-07
    cd = fmt.export_filename(' 信号 / 群:*?"<>| ', "alerts\tnow\x01", 201, T, late, SHANGHAI, "csv")
    assert cd.filename == "信号_群_alerts_now_2026-11-06_2026-11-07.csv"
    assert cd.ascii_filename == "channel-201_2026-11-06_2026-11-07.csv"
    header = cd.header()
    assert header.startswith('attachment; filename="channel-201_2026-11-06_2026-11-07.csv"; ')
    assert "filename*=UTF-8''%E4%BF%A1%E5%8F%B7_" in header
    assert "\n" not in header and "\r" not in header


def test_filename_empty_and_long_names() -> None:
    assert fmt.clean_name("///", "server") == "server"
    assert fmt.clean_name("", "channel") == "channel"
    assert fmt.clean_name("a" * 50, "x") == "a" * 30
    emoji = "😀" * 40  # 每个 4 字节
    cleaned = fmt.clean_name(emoji, "x")
    assert len(cleaned.encode()) <= 100 and set(cleaned) == {"😀"}
    cd = fmt.export_filename(emoji, emoji, 1, T, T, UTC, "jsonl")
    assert len(cd.filename.encode()) <= 255


# --- CSV（AC-13、AC-15、AC-17） ----------------------------------------------


def test_csv_header_has_bom_and_crlf() -> None:
    header = fmt.csv_header()
    assert header.startswith("﻿id,created_at,author_id,")
    assert header.endswith("deleted_at\r\n")


def test_csv_row_basic_columns() -> None:
    row = fmt.csv_row(
        msg(
            content='他说 "hi", <@1>\n第二行',
            reply_to_id=7,
            version=2,
            edited_at=T,
            deleted_at=T + timedelta(minutes=1),
        ),
        SHANGHAI,
        SITE,
    )
    assert row.endswith("\r\n")
    cells = parse_csv_row(row)
    assert cells["id"] == "1300000000000000001"
    assert cells["created_at"] == "2026-11-06T15:30:00+08:00"
    assert cells["content"] == '他说 "hi", \n第二行'
    assert cells["reply_to_id"] == "7"
    assert cells["version"] == "2"
    assert cells["edited_at"] == "2026-11-06T15:30:00+08:00"
    assert cells["deleted_at"] == "2026-11-06T15:31:00+08:00"


@pytest.mark.parametrize("content", ['=HYPERLINK("x")', "-1", "+1", "@x", "\tx", "\rx"])
def test_csv_formula_guard(content: str) -> None:
    cells = parse_csv_row(fmt.csv_row(msg(content=content, author_name="=bad"), UTC, SITE))
    assert cells["content"] == "'" + content
    assert cells["author_name"] == "'=bad"
    assert cells["id"] == "1300000000000000001"


def test_csv_embeds_attachments_and_forwarded() -> None:
    embed = {
        "title": "Title <@1>",
        "description": "Desc",
        "fields": [{"name": "Price", "value": "<:up:3> 65k"}],
        "author": {"name": "Bot"},
        "footer_text": "Foot",
    }
    forwarded = {
        "message_id": 9,
        "channel_id": 8,
        "guild_id": None,
        "content": "fwd <#5>",
        "embeds": [],
        "attachment_names": ["a.png", "b.png"],
    }
    message = msg(
        embeds=(embed, {"title": "Second"}),
        forwarded=forwarded,
        attachments=(att(1), att(2), att(3, removed=True)),
        media=(
            media(11, "stored", attachment_id=1),
            media(12, "pending", attachment_id=2),
            media(21, "stored", slot=1),
        ),
    )
    cells = parse_csv_row(fmt.csv_row(message, UTC, SITE))  # type: ignore[arg-type]
    assert cells["embeds"] == "Bot\nTitle \nDesc\nPrice: :up: 65k\nFoot\n\nSecond"
    assert cells["attachments"] == f"f1.png {SITE}/api/media/11\nf2.png"
    assert cells["forwarded"] == f"fwd \na.png\nb.png {SITE}/api/media/21"


# --- embed 图片副本（AC-17） -------------------------------------------------


def test_embed_media_urls_match_slot_and_status() -> None:
    embeds = (
        {"title": "a", "image_url": "https://cdn/a.png"},
        {"title": "b", "image_url": "https://cdn/b.png", "thumbnail_url": "https://cdn/t.png"},
        {"title": "c", "image_url": "https://cdn/c.png"},
    )
    rows = (
        embed_media(31, "embed_image", 1),
        embed_media(32, "embed_thumbnail", 1),
        embed_media(33, "embed_image", 2, "pending"),
    )
    result = fmt.with_embed_media_urls(embeds, rows, SITE)  # type: ignore[arg-type]
    assert "image_media_url" not in result[0]
    assert result[1]["image_media_url"] == f"{SITE}/api/media/31"  # type: ignore[typeddict-item]
    assert result[1]["thumbnail_media_url"] == f"{SITE}/api/media/32"  # type: ignore[typeddict-item]
    assert result[1]["image_url"] == "https://cdn/b.png"
    assert "image_media_url" not in result[2]
    assert "image_media_url" not in embeds[1]  # 不改入参


def test_embed_media_urls_in_jsonl_and_csv_but_not_revisions_or_forwarded() -> None:
    message = msg(
        embeds=({"description": "d", "image_url": "https://cdn/a.png"},),
        forwarded={
            "message_id": 9,
            "channel_id": 8,
            "guild_id": None,
            "content": "",
            "embeds": [{"image_url": "https://cdn/f.png"}],
            "attachment_names": [],
        },
        media=(embed_media(31, "embed_image", 0), embed_media(32, "embed_thumbnail", 0)),
        revisions=(ExportRevision(1, "old", ({"image_url": "https://cdn/o.png"},), T),),
    )
    record = json.loads(fmt.jsonl_line(message, UTC, SITE))  # type: ignore[arg-type]
    assert record["embeds"][0]["image_url"] == "https://cdn/a.png"
    assert record["embeds"][0]["image_media_url"] == f"{SITE}/api/media/31"
    assert record["embeds"][0]["thumbnail_media_url"] == f"{SITE}/api/media/32"
    assert "image_media_url" not in record["forwarded"]["embeds"][0]
    assert "image_media_url" not in record["revisions"][0]["embeds"][0]
    cells = parse_csv_row(fmt.csv_row(message, UTC, SITE))  # type: ignore[arg-type]
    assert cells["embeds"] == f"d\n图片: {SITE}/api/media/31\n缩略图: {SITE}/api/media/32"


def test_csv_embed_with_only_image_copy() -> None:
    message = msg(
        embeds=({"image_url": "https://cdn/a.png"},), media=(embed_media(31, "embed_image", 0),)
    )
    cells = parse_csv_row(fmt.csv_row(message, UTC, SITE))  # type: ignore[arg-type]
    assert cells["embeds"] == f"图片: {SITE}/api/media/31"


# --- JSONL（AC-16、AC-17） --------------------------------------------------


def test_jsonl_line_shape() -> None:
    message = msg(
        content="中文 <@1>",
        embeds=({"title": "<:x:1>", "timestamp": "2026-01-01T00:00:00Z", "image_url": "u"},),
        forwarded={
            "message_id": 9,
            "channel_id": 8,
            "guild_id": 7,
            "content": "f",
            "embeds": [],
            "attachment_names": ["a.png", "b.png"],
        },
        attachments=(att(1), att(3, removed=True)),
        media=(media(11, "stored", attachment_id=1), media(21, "too_large", slot=0)),
        version=3,
        revisions=(
            ExportRevision(1, "v1 <#2>", (), T),
            ExportRevision(2, "v2", ({"title": "t"},), T + timedelta(minutes=1)),
        ),
    )
    line = fmt.jsonl_line(message, SHANGHAI, SITE)  # type: ignore[arg-type]
    assert line.endswith("\n") and line.count("\n") == 1
    assert "中文" in line  # 不转义
    record = json.loads(line)
    assert record["id"] == "1300000000000000001"
    assert record["guild_id"] == "100"
    assert record["author"] == {"id": "300", "name": "KOL"}
    assert record["content"] == "中文 "
    assert record["embeds"] == [
        {"title": ":x:", "timestamp": "2026-01-01T00:00:00Z", "image_url": "u"}
    ]
    assert [a["id"] for a in record["attachments"]] == ["1", "3"]
    assert record["attachments"][0]["url"] == f"{SITE}/api/media/11"
    assert record["attachments"][0]["media_status"] == "stored"
    assert record["attachments"][1]["removed_at"] == "2026-11-06T15:30:00+08:00"
    assert record["attachments"][1]["media_status"] is None
    fwd = record["forwarded"]
    assert fwd["message_id"] == "9" and fwd["guild_id"] == "7"
    assert fwd["attachments"][0] == {
        "filename": "a.png",
        "content_type": "image/jpeg",
        "size_bytes": 99,
        "media_status": "too_large",
        "url": None,
    }
    assert fwd["attachments"][1]["media_status"] is None
    assert [r["version"] for r in record["revisions"]] == [1, 2]
    assert record["revisions"][0]["content"] == "v1 "
    assert record["revisions"][1]["replaced_at"] == "2026-11-06T15:31:00+08:00"


def test_jsonl_no_formula_guard_and_empty_revisions() -> None:
    record = json.loads(fmt.jsonl_line(msg(content="=1"), UTC, SITE))
    assert record["content"] == "=1"
    assert record["revisions"] == []
    assert record["forwarded"] is None
