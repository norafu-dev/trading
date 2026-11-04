import { describe, expect, it } from "vitest";

import {
  batchStatusSummary,
  checkRange,
  countChannels,
  describeCreate,
  filterGuilds,
  groupByBatch,
  type HistoryJob,
  localInputToIso,
  percent,
  presetInputs,
  splitByCollect,
  toLocalInput,
} from "./describe-history";

describe("presetInputs", () => {
  // covers: spec 0012 AC-1
  it("快捷项按本地时间算出起止时间", () => {
    const now = new Date(2026, 10, 5, 9, 30);
    expect(presetInputs("24h", now)).toEqual({
      start: "2026-11-04T09:30",
      end: "2026-11-05T09:30",
      allHistory: false,
    });
    expect(presetInputs("7d", now).start).toBe("2026-10-29T09:30");
    expect(presetInputs("30d", now).start).toBe("2026-10-06T09:30");
    expect(presetInputs("all", now)).toEqual({ start: "", end: "", allHistory: true });
  });

  it("本地输入和 ISO 时间互相转换", () => {
    const local = new Date(2026, 0, 2, 3, 4);
    expect(toLocalInput(local)).toBe("2026-01-02T03:04");
    expect(localInputToIso("2026-01-02T03:04")).toBe(local.toISOString());
    expect(localInputToIso("")).toBeNull();
    expect(localInputToIso("not a date")).toBeNull();
  });
});

describe("checkRange", () => {
  // covers: spec 0012 AC-1
  it("全部历史只带结束时间，结束为空表示现在", () => {
    expect(checkRange({ start: "", end: "", allHistory: true })).toEqual({
      ok: true,
      range_start: null,
      range_end: null,
    });
  });

  it("起点必填并且要早于终点", () => {
    expect(checkRange({ start: "", end: "", allHistory: false })).toEqual({
      ok: false,
      message: "请填写开始时间",
    });
    expect(
      checkRange({ start: "2026-01-02T00:00", end: "2026-01-01T00:00", allHistory: false }),
    ).toEqual({ ok: false, message: "开始时间要早于结束时间" });
    const ok = checkRange({ start: "2026-01-01T00:00", end: "", allHistory: false });
    expect(ok).toEqual({
      ok: true,
      range_start: localInputToIso("2026-01-01T00:00"),
      range_end: null,
    });
  });
});

const JOB: HistoryJob = {
  id: "1",
  batch_id: "b1",
  channel_id: "201",
  guild_name: "Signals",
  channel_name: "alerts",
  origin: "manual",
  refetch: false,
  range_start: "2026-01-01T00:00:00Z",
  range_end: "2026-01-02T00:00:00Z",
  status: "running",
  fetched_count: 100,
  pages_fetched: 1,
  cursor_at: null,
  progress: 0.4,
  wait_reason: null,
  wait_until: null,
  not_before: null,
  error: null,
  created_at: "2026-01-03T00:00:00Z",
  started_at: null,
  finished_at: null,
};

describe("列表辅助", () => {
  // covers: spec 0012 AC-14
  it("按批次分组，保持出现顺序", () => {
    const groups = groupByBatch([
      JOB,
      { ...JOB, id: "2", batch_id: "b2" },
      { ...JOB, id: "3", batch_id: "b1" },
    ]);
    expect(groups.map((g) => [g.batchId, g.jobs.map((j) => j.id)])).toEqual([
      ["b1", ["1", "3"]],
      ["b2", ["2"]],
    ]);
  });

  it("批次状态只列不为 0 的", () => {
    expect(
      batchStatusSummary({
        batch_id: "b1",
        channel_count: 2,
        fetched_count: 10,
        status_counts: { pending: 1, running: 1, paused: 0, done: 2, failed: 0, cancelled: 0 },
      }),
    ).toBe("抓取中 1 · 排队中 1 · 已完成 2");
  });

  it("进度百分比和提交结果说明", () => {
    expect(percent(0.404)).toBe(40);
    expect(percent(1.2)).toBe(100);
    expect(describeCreate({ batch_id: null, jobs: [], skipped: [] })).toBe("没有需要采的时间");
    expect(describeCreate({ batch_id: "b1", jobs: [JOB, { ...JOB, id: "2" }], skipped: [] })).toBe(
      "已为 1 个频道建了 2 个任务",
    );
  });
});

describe("频道选择", () => {
  const guilds = [
    {
      id: "100",
      name: "Signals",
      channels: [
        { id: "1", name: "alerts", collect_enabled: true },
        { id: "2", name: "news", collect_enabled: false },
      ],
    },
    { id: "110", name: "Other", channels: [{ id: "3", name: "General", collect_enabled: false }] },
  ];

  // covers: spec 0012 AC-1
  it("按是否开了实时采集拆成两组，仍按服务器分组", () => {
    const { collecting, others } = splitByCollect(guilds);
    expect(collecting.map((g) => [g.id, g.channels.map((c) => c.id)])).toEqual([["100", ["1"]]]);
    expect(others.map((g) => [g.id, g.channels.map((c) => c.id)])).toEqual([
      ["100", ["2"]],
      ["110", ["3"]],
    ]);
    expect(countChannels(others)).toBe(2);
  });

  it("搜索频道名或服务器名，不分大小写", () => {
    expect(filterGuilds(guilds, "general").map((g) => g.id)).toEqual(["110"]);
    expect(filterGuilds(guilds, "SIGNALS")[0]?.channels).toHaveLength(2);
    expect(filterGuilds(guilds, "  ")).toBe(guilds);
    expect(filterGuilds(guilds, "zzz")).toEqual([]);
  });
});
