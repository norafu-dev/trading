import { describe, expect, it } from "vitest";

import {
  buildExportUrl,
  type ExportSelection,
  markFor,
  NO_SELECTION,
  selectMessage,
  toEnd,
  toggleOpen,
  toRangeParams,
} from "./export-selection";

const msg = (id: string) => ({ id, createdAt: "2026-11-06T07:30:00Z", authorName: `u${id}` });
const A = toEnd(msg("1300000000000000001"));
const B = toEnd(msg("1300000000000000005"));
const M = msg("1300000000000000003");
const OPEN = { kind: "open" } as const;

const one = (first = A): ExportSelection => ({ step: "one", first });
const oneOpen = (side: "start" | "end"): ExportSelection => ({ step: "one-open", side });
const ready = (start: unknown, end: unknown) => ({ step: "ready", start, end }) as ExportSelection;

describe("selectMessage（AC-2、AC-3）", () => {
  it("none → one", () => {
    expect(selectMessage(NO_SELECTION, M)).toEqual(one(toEnd(M)));
  });

  it("one + 新的（比第一端晚或早）→ 按 ID 排序，和先后无关", () => {
    expect(selectMessage(one(A), M)).toEqual(ready(A, toEnd(M)));
    expect(selectMessage(one(B), M)).toEqual(ready(toEnd(M), B));
  });

  it("同一条点两次 → 只导出这一条", () => {
    expect(selectMessage(one(A), msg(A.id))).toEqual(ready(A, A));
  });

  it("消息 ID 超过安全整数时仍按数值排序", () => {
    const big = toEnd(msg("9000000000000000001"));
    const bigger = msg("9000000000000000002");
    expect(selectMessage(one(toEnd(bigger)), big)).toEqual(ready(big, toEnd(bigger)));
  });

  it("one-open：只替换另一端", () => {
    expect(selectMessage(oneOpen("start"), M)).toEqual(ready(OPEN, toEnd(M)));
    expect(selectMessage(oneOpen("end"), M)).toEqual(ready(toEnd(M), OPEN));
  });

  it("ready(A, B) + 第三条 → 以它为新的第一端重新开始", () => {
    expect(selectMessage(ready(A, B), M)).toEqual(one(toEnd(M)));
  });

  it("有一端开放：替换消息那一端；两端都开放时替换终点", () => {
    expect(selectMessage(ready(OPEN, B), M)).toEqual(ready(OPEN, toEnd(M)));
    expect(selectMessage(ready(A, OPEN), M)).toEqual(ready(toEnd(M), OPEN));
    expect(selectMessage(ready(OPEN, OPEN), M)).toEqual(ready(OPEN, toEnd(M)));
  });
});

describe("toggleOpen（AC-3）", () => {
  it("none", () => {
    expect(toggleOpen(NO_SELECTION, "start")).toEqual(oneOpen("start"));
    expect(toggleOpen(NO_SELECTION, "end")).toEqual(oneOpen("end"));
  });

  it("one(A)", () => {
    expect(toggleOpen(one(A), "start")).toEqual(ready(OPEN, A));
    expect(toggleOpen(one(A), "end")).toEqual(ready(A, OPEN));
  });

  it("one-open：再点同一个取消，点另一个变成整个频道", () => {
    expect(toggleOpen(oneOpen("start"), "start")).toEqual(NO_SELECTION);
    expect(toggleOpen(oneOpen("start"), "end")).toEqual(ready(OPEN, OPEN));
    expect(toggleOpen(oneOpen("end"), "end")).toEqual(NO_SELECTION);
    expect(toggleOpen(oneOpen("end"), "start")).toEqual(ready(OPEN, OPEN));
  });

  it("ready(A, B)：替换对应的一端，另一端保留", () => {
    expect(toggleOpen(ready(A, B), "start")).toEqual(ready(OPEN, B));
    expect(toggleOpen(ready(A, B), "end")).toEqual(ready(A, OPEN));
  });

  it("ready(open, B)", () => {
    expect(toggleOpen(ready(OPEN, B), "start")).toEqual(one(B));
    expect(toggleOpen(ready(OPEN, B), "end")).toEqual(ready(OPEN, OPEN));
  });

  it("ready(A, open)", () => {
    expect(toggleOpen(ready(A, OPEN), "start")).toEqual(ready(OPEN, OPEN));
    expect(toggleOpen(ready(A, OPEN), "end")).toEqual(one(A));
  });

  it("ready(open, open)：取消点的那一边，留下另一边", () => {
    expect(toggleOpen(ready(OPEN, OPEN), "start")).toEqual(oneOpen("end"));
    expect(toggleOpen(ready(OPEN, OPEN), "end")).toEqual(oneOpen("start"));
  });
});

describe("toRangeParams / buildExportUrl", () => {
  it("只有 ready 才有参数，开放的一端不传", () => {
    expect(toRangeParams(NO_SELECTION)).toBeNull();
    expect(toRangeParams(one(A))).toBeNull();
    expect(toRangeParams(oneOpen("start"))).toBeNull();
    expect(toRangeParams(ready(A, B))).toEqual({ start: A.id, end: B.id });
    expect(toRangeParams(ready(OPEN, B))).toEqual({ end: B.id });
    expect(toRangeParams(ready(A, OPEN))).toEqual({ start: A.id });
    expect(toRangeParams(ready(OPEN, OPEN))).toEqual({});
  });

  it("下载网址带 start、end、format、tz", () => {
    expect(buildExportUrl("12", { start: "1", end: "2" }, "csv", "Asia/Shanghai")).toBe(
      "/api/channels/12/export?start=1&end=2&format=csv&tz=Asia%2FShanghai",
    );
    expect(buildExportUrl("12", {}, "jsonl", "UTC")).toBe(
      "/api/channels/12/export?format=jsonl&tz=UTC",
    );
  });
});

describe("markFor（AC-4）", () => {
  it("one 只标第一条", () => {
    expect(markFor(one(A), A.id)).toBe("picked");
    expect(markFor(one(A), M.id)).toBeNull();
  });

  it("ready：起点、终点、范围内、范围外", () => {
    const sel = ready(A, B);
    expect(markFor(sel, A.id)).toBe("start");
    expect(markFor(sel, B.id)).toBe("end");
    expect(markFor(sel, M.id)).toBe("range");
    expect(markFor(sel, "1300000000000000009")).toBeNull();
    expect(markFor(sel, "1299999999999999999")).toBeNull();
  });

  it("同一条是 single；开放的一端不限制", () => {
    expect(markFor(ready(A, A), A.id)).toBe("single");
    expect(markFor(ready(OPEN, B), "1")).toBe("range");
    expect(markFor(ready(OPEN, B), "1300000000000000009")).toBeNull();
    expect(markFor(ready(A, OPEN), "1300000000000000009")).toBe("range");
    expect(markFor(ready(OPEN, OPEN), M.id)).toBe("range");
  });

  it("没有选择时没有标记", () => {
    expect(markFor(NO_SELECTION, M.id)).toBeNull();
    expect(markFor(oneOpen("end"), M.id)).toBeNull();
  });
});
