import { describe, expect, it } from "vitest";

import {
  batchActions,
  batchStatusSummary,
  checkRange,
  checkSettings,
  countChannels,
  describeCreate,
  filterGuilds,
  groupByBatch,
  type HistoryJob,
  type HistoryStatus,
  isRateLimitedPause,
  jobActions,
  localInputToIso,
  percent,
  presetInputs,
  type SettingsInput,
  splitByCollect,
  statusNotices,
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

// ---- 总开关、节奏设置 -----------------------------------------------------------------

const fullTime = (iso: string) => `完整(${iso})`;
const STATUS: HistoryStatus = {
  paused_reason: null,
  paused_at: null,
  waiting_reason: null,
  waiting_until: null,
  pages_used_24h: 10,
  daily_page_limit: 1500,
  quota_resumes_at: null,
  last_seen_at: "2026-11-05T10:00:00Z",
  stale: false,
};

describe("statusNotices", () => {
  it("正常运行时没有提示", () => {
    expect(statusNotices(STATUS, fullTime)).toEqual([]);
  });

  // covers: spec 0012 AC-8
  it("三种暂停原因各有文字和暂停时间，只有手动暂停不标红", () => {
    const at = "2026-11-05T09:00:00Z";
    const manual = statusNotices({ ...STATUS, paused_reason: "manual", paused_at: at }, fullTime);
    expect(manual).toEqual([{ key: "paused", tone: "info", text: `你手动暂停（完整(${at})）` }]);
    const limited = statusNotices(
      { ...STATUS, paused_reason: "rate_limited", paused_at: at },
      fullTime,
    );
    expect(limited[0]).toMatchObject({ tone: "error", text: `因限速自动暂停（完整(${at})）` });
    const risk = statusNotices(
      { ...STATUS, paused_reason: "account_risk", paused_at: at },
      fullTime,
    );
    expect(risk[0]?.text).toContain("账号可能有风险，已自动暂停");
  });

  // covers: spec 0012 AC-6
  it("休息中和额度用完都说明最早继续的时间", () => {
    const rest = statusNotices(
      { ...STATUS, waiting_reason: "rest", waiting_until: "2026-11-05T10:05:00Z" },
      fullTime,
    );
    expect(rest[0]?.text).toBe("休息中，完整(2026-11-05T10:05:00Z) 继续");
    const quota = statusNotices(
      { ...STATUS, waiting_reason: "quota", waiting_until: "2026-11-06T01:00:00Z" },
      fullTime,
    );
    expect(quota[0]?.text).toBe("今日额度已用完，最早 完整(2026-11-06T01:00:00Z) 继续");
  });

  it("用量到了上限但还没进入等待时，也用预计继续时间提示额度用完", () => {
    const notices = statusNotices(
      { ...STATUS, pages_used_24h: 1500, quota_resumes_at: "2026-11-06T01:00:00Z" },
      fullTime,
    );
    expect(notices[0]?.text).toContain("今日额度已用完");
  });

  // covers: spec 0012 AC-20
  it("历史任务无响应时标红", () => {
    expect(statusNotices({ ...STATUS, stale: true }, fullTime)[0]).toMatchObject({
      key: "stale",
      tone: "error",
    });
  });
});

describe("isRateLimitedPause", () => {
  // covers: spec 0012 AC-7
  it("只有限速自动暂停才亮红点", () => {
    expect(isRateLimitedPause({ ...STATUS, paused_reason: "rate_limited" })).toBe(true);
    expect(isRateLimitedPause({ ...STATUS, paused_reason: "manual" })).toBe(false);
    expect(isRateLimitedPause({ ...STATUS, paused_reason: "account_risk" })).toBe(false);
    expect(isRateLimitedPause(undefined)).toBe(false);
  });
});

describe("checkSettings", () => {
  const OK: SettingsInput = {
    page_delay_min_s: "2",
    page_delay_max_s: "4",
    daily_page_limit: "1500",
    run_minutes: "20",
    rest_minutes: "5",
    auto_gap_max_hours: "6",
  };

  // covers: spec 0012 AC-19
  it("范围内的整数通过", () => {
    expect(checkSettings(OK)).toEqual({
      ok: true,
      values: {
        page_delay_min_s: 2,
        page_delay_max_s: 4,
        daily_page_limit: 1500,
        run_minutes: 20,
        rest_minutes: 5,
        auto_gap_max_hours: 6,
      },
    });
  });

  // covers: spec 0012 AC-19
  it("边界值通过，自动补缺口填 0 表示关闭", () => {
    const check = checkSettings({
      page_delay_min_s: "1",
      page_delay_max_s: "120",
      daily_page_limit: "20000",
      run_minutes: "5",
      rest_minutes: "120",
      auto_gap_max_hours: "0",
    });
    expect(check.ok).toBe(true);
  });

  // covers: spec 0012 AC-19
  it.each([
    ["page_delay_min_s", "0"],
    ["page_delay_max_s", "121"],
    ["daily_page_limit", "49"],
    ["daily_page_limit", "20001"],
    ["run_minutes", "4"],
    ["rest_minutes", "0"],
    ["auto_gap_max_hours", "73"],
    ["run_minutes", "abc"],
    ["rest_minutes", "1.5"],
    ["daily_page_limit", ""],
  ] as const)("%s 填 %s 时不通过并说明范围", (key, value) => {
    const check = checkSettings({ ...OK, [key]: value });
    expect(check.ok).toBe(false);
    if (!check.ok) expect(check.errors[key]).toMatch(/请输入 \d+ 到 \d+ 之间的整数/);
  });

  // covers: spec 0012 AC-19
  it("最短间隔大于最长间隔时不通过", () => {
    const check = checkSettings({ ...OK, page_delay_min_s: "9", page_delay_max_s: "4" });
    expect(check).toEqual({
      ok: false,
      errors: { page_delay_min_s: "最短间隔不能大于最长间隔" },
    });
  });
});

describe("任务操作", () => {
  it("每个状态只给服务端允许的操作", () => {
    expect(jobActions("pending")).toEqual(["pause", "cancel"]);
    expect(jobActions("running")).toEqual(["pause", "cancel"]);
    expect(jobActions("paused")).toEqual(["resume", "cancel"]);
    expect(jobActions("failed")).toEqual(["retry", "cancel"]);
    expect(jobActions("done")).toEqual([]);
    expect(jobActions("cancelled")).toEqual([]);
  });

  it("整批操作只列至少有一行符合的，没有重试", () => {
    const counts = { pending: 0, running: 0, paused: 0, done: 0, failed: 0, cancelled: 0 };
    const batch = (patch: Partial<typeof counts>) => ({
      batch_id: "b",
      channel_count: 1,
      fetched_count: 0,
      status_counts: { ...counts, ...patch },
    });
    expect(batchActions(batch({ running: 1 }))).toEqual(["pause", "cancel"]);
    expect(batchActions(batch({ paused: 2 }))).toEqual(["resume", "cancel"]);
    expect(batchActions(batch({ failed: 1 }))).toEqual(["cancel"]);
    expect(batchActions(batch({ done: 3, cancelled: 1 }))).toEqual([]);
  });
});
