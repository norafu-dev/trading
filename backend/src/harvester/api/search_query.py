"""搜索条件的解析（spec 0013）：纯函数，前端 `parse-query.ts` 和 `search-params.ts` 有同样规则。

两边共用 `tests/fixtures/search_query_cases.json` 保证一致。导出功能直接复用这里。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from harvester.db.search import SearchFilters
from harvester.db.snapshots import DISCORD_EPOCH

MAX_QUERY_CHARS = 200
MAX_TERMS = 10
MIN_TERM_CHARS = 2
MAX_GUILDS = 50
MAX_CHANNELS = 200
MAX_AUTHORS = 20
_BIGINT_MAX = 2**63 - 1

# 前后端共用同一组空白字符（Python 的 \s 和 JS 的 \s 不完全一样，所以写死）。
_WS_CHARS = "\t\n\v\f\r    -     　﻿"
_WS = re.compile(f"[{_WS_CHARS}]+")
_WS_STRIP = "\t\n\v\f\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff" + "".join(
    chr(c) for c in range(0x2000, 0x200B)
)
_WS_ONE = re.compile(f"[{_WS_CHARS}]")

QueryReason = Literal["too_long", "too_many_terms", "term_too_short", "exclude_only"]


@dataclass(frozen=True)
class ParsedQuery:
    include: tuple[str, ...]
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class QueryError:
    reason: QueryReason
    term: str | None = None


@dataclass(frozen=True)
class FieldError:
    loc: tuple[str, ...]
    type: str


@dataclass(frozen=True)
class FilterError:
    """code 是 invalid_query 或 invalid_filters。"""

    code: Literal["invalid_query", "invalid_filters"]
    fields: tuple[FieldError, ...]


def _normalize(raw: str) -> str:
    return _WS.sub(" ", raw).strip(_WS_STRIP).lower()


def _tokenize(q: str) -> list[tuple[bool, str]]:
    """返回 (是否排除, 词) 列表，按出现顺序，已经规范化，空的丢掉（AC-3）。"""
    tokens: list[tuple[bool, str]] = []
    i, n = 0, len(q)
    while i < n:
        if _WS_ONE.match(q[i]):
            i += 1
            continue
        negate = False
        if q[i] == "-":
            negate = True
            i += 1
            if i >= n or _WS_ONE.match(q[i]):
                continue  # 单独的 -
        if q[i] == '"':
            end = q.find('"', i + 1)
            if end == -1:
                end = n  # 没闭合：到结尾
            raw = q[i + 1 : end]
            i = min(end + 1, n)
        else:
            j = i
            while j < n and not _WS_ONE.match(q[j]):
                j += 1
            raw = q[i:j]
            i = j
        term = _normalize(raw)
        if term:
            tokens.append((negate, term))
    return tokens


def parse_query(q: str) -> ParsedQuery | QueryError:
    if len(q) > MAX_QUERY_CHARS:
        return QueryError("too_long")
    tokens = list(dict.fromkeys(_tokenize(q)))  # 去重，保持顺序
    if len(tokens) > MAX_TERMS:
        return QueryError("too_many_terms")
    for _, term in tokens:
        if len(term) < MIN_TERM_CHARS:
            return QueryError("term_too_short", term)
    return ParsedQuery(
        include=tuple(t for neg, t in tokens if not neg),
        exclude=tuple(t for neg, t in tokens if neg),
    )


def parse_ids(raw: str | None, name: str, limit: int) -> tuple[tuple[int, ...], FieldError | None]:
    if raw is None or raw == "":
        return (), None
    parts = raw.split(",")
    for part in parts:
        if not (part.isascii() and part.isdigit() and len(part) <= 19):
            return (), FieldError(("query", name), "invalid_id")
        if not 1 <= int(part) <= _BIGINT_MAX:
            return (), FieldError(("query", name), "invalid_id")
    ids = tuple(dict.fromkeys(int(p) for p in parts))
    if len(ids) > limit:
        return (), FieldError(("query", name), "too_many_ids")
    return ids, None


def _parse_datetime(raw: str | None, name: str) -> tuple[datetime | None, FieldError | None]:
    if raw is None or raw == "":
        return None, None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None, FieldError(("query", name), "invalid_datetime")
    if value.tzinfo is None:
        return None, FieldError(("query", name), "invalid_datetime")
    return max(value.astimezone(UTC), DISCORD_EPOCH), None


def parse_filters(
    q: str | None,
    guild: str | None,
    channel: str | None,
    author: str | None,
    time_from: str | None,
    time_to: str | None,
) -> SearchFilters | FilterError:
    """所有参数按原始字符串收进来，手动解析，出错时返回固定的错误码和 fields。"""
    parsed = parse_query(q or "")
    if isinstance(parsed, QueryError):
        return FilterError("invalid_query", (FieldError(("query", "q"), parsed.reason),))

    errors: list[FieldError] = []
    guilds, err = parse_ids(guild, "guild", MAX_GUILDS)
    errors += [err] if err else []
    channels, err = parse_ids(channel, "channel", MAX_CHANNELS)
    errors += [err] if err else []
    authors, err = parse_ids(author, "author", MAX_AUTHORS)
    errors += [err] if err else []
    start, err = _parse_datetime(time_from, "from")
    errors += [err] if err else []
    end, err = _parse_datetime(time_to, "to")
    errors += [err] if err else []
    if start is not None and end is not None and start >= end:
        errors.append(FieldError(("query", "from"), "empty_range"))
    if errors:
        return FilterError("invalid_filters", tuple(errors))

    has_filter = bool(guilds or channels or authors or start or end)
    if not parsed.include and not parsed.exclude and not has_filter:
        return FilterError("invalid_filters", (FieldError(("query",), "no_conditions"),))
    if not parsed.include and not has_filter:
        return FilterError("invalid_query", (FieldError(("query", "q"), "exclude_only"),))
    return SearchFilters(
        include=parsed.include,
        exclude=parsed.exclude,
        guilds=guilds,
        channels=channels,
        authors=authors,
        time_from=start,
        time_to=end,
    )


def fields_payload(fields: Sequence[FieldError]) -> list[dict[str, object]]:
    return [{"loc": list(f.loc), "type": f.type} for f in fields]
