import { describe, expect, it } from "vitest";

import {
  EMPTY_FILTERS,
  parseSearchParams,
  resolveRange,
  type SearchFilters,
  toSearchParams,
} from "./search-params";

describe("search-params", () => {
  it("往返不丢条件", () => {
    const filters: SearchFilters = {
      ...EMPTY_FILTERS,
      q: '空投 -"止损 位"',
      guilds: ["1"],
      channels: ["2", "3"],
      authors: ["4"],
      range: "7d",
    };
    expect(parseSearchParams(toSearchParams(filters))).toEqual(filters);
  });

  it("不合法的值丢掉，重复的去重", () => {
    const parsed = parseSearchParams(
      new URLSearchParams("guild=1,x,1,0,9223372036854775808&from=oops&range=bad"),
    );
    expect(parsed.guilds).toEqual(["1"]);
    expect(parsed.from).toBeNull();
    expect(parsed.range).toBeNull();
  });

  it("from/to 优先于 range", () => {
    const parsed = parseSearchParams(new URLSearchParams("range=7d&from=2026-01-01T00:00:00Z"));
    expect(parsed.range).toBeNull();
    expect(toSearchParams(parsed).get("range")).toBeNull();
  });

  it("快捷项换算成固定时间", () => {
    const now = new Date("2026-11-05T12:00:00Z");
    expect(resolveRange({ ...EMPTY_FILTERS, range: "7d" }, now).from).toBe(
      "2026-10-29T12:00:00.000Z",
    );
    expect(resolveRange({ ...EMPTY_FILTERS, range: "today" }, now).to).toBeNull();
    expect(resolveRange(EMPTY_FILTERS, now)).toEqual({ from: null, to: null });
  });
});
