"""spec 0013 AC-3、AC-4、AC-26：关键词和筛选参数的解析（纯函数）。"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harvester.api.search_query import (
    FilterError,
    ParsedQuery,
    QueryError,
    parse_filters,
    parse_query,
)
from harvester.db.snapshots import DISCORD_EPOCH

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "search_query_cases.json").read_text("utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["q"][:30])
def test_shared_cases(case: dict[str, Any]) -> None:  # covers: AC-3, AC-4, AC-26
    result = parse_query(case["q"])
    if "error" in case:
        assert isinstance(result, QueryError)
        assert result.reason == case["error"]
        assert result.term == case.get("term")
    else:
        assert result == ParsedQuery(tuple(case["include"]), tuple(case["exclude"]))


def _filters(**kw: str | None) -> Any:
    args: dict[str, str | None] = dict.fromkeys(("q", "guild", "channel", "author", "from", "to"))
    args.update(kw)
    return parse_filters(
        args["q"], args["guild"], args["channel"], args["author"], args["from"], args["to"]
    )


def _types(error: FilterError) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(error.code, f.type, f.loc) for f in error.fields]


def test_no_conditions() -> None:
    result = _filters()
    assert isinstance(result, FilterError)
    assert _types(result) == [("invalid_filters", "no_conditions", ("query",))]


def test_exclude_only_needs_a_filter() -> None:  # covers: AC-4
    result = _filters(q="-spam")
    assert isinstance(result, FilterError)
    assert _types(result) == [("invalid_query", "exclude_only", ("query", "q"))]
    assert not isinstance(_filters(q="-spam", guild="1"), FilterError)


def test_filter_only_is_valid() -> None:  # covers: AC-2
    result = _filters(author="5,5,6")
    assert not isinstance(result, FilterError)
    assert result.authors == (5, 6)


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"guild": "abc"}, "invalid_id"),
        ({"channel": "1,,2"}, "invalid_id"),
        ({"author": str(2**63)}, "invalid_id"),
        ({"author": ",".join(str(i) for i in range(1, 22))}, "too_many_ids"),
        ({"from": "2026-01-01T00:00:00"}, "invalid_datetime"),
        ({"to": "yesterday"}, "invalid_datetime"),
        ({"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, "empty_range"),
    ],
)
def test_filter_errors(kw: dict[str, str], expected: str) -> None:  # covers: AC-10, AC-26
    result = _filters(q="btc", **kw)
    assert isinstance(result, FilterError)
    assert result.code == "invalid_filters"
    assert result.fields[0].type == expected


def test_before_epoch_is_clamped_and_future_from_is_valid() -> None:  # covers: AC-10
    result = _filters(**{"from": "2001-01-01T00:00:00Z", "to": "2099-01-01T00:00:00+08:00"})
    assert not isinstance(result, FilterError)
    assert result.time_from == DISCORD_EPOCH
    assert result.time_to == datetime(2098, 12, 31, 16, tzinfo=UTC)
