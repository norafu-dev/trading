import { describe, expect, it } from "vitest";

import { describeHistoryWorker, describeMediaWorker, type TimeText } from "./describe-system";
import type { SystemStatusData } from "./use-system-status";

const time: TimeText = { relative: (iso) => `相对(${iso})`, full: (iso) => `完整(${iso})` };
const MEDIA: SystemStatusData["media"] = {
  storage_full: false,
  last_seen_at: "2026-11-04T10:00:00Z",
  stale: false,
  paused_until: null,
};

describe("describeMediaWorker", () => {
  // covers: spec 0011 AC-22
  it("正常时显示最近心跳的相对时间，悬停是完整时间", () => {
    expect(describeMediaWorker(MEDIA, time)).toEqual({
      tone: "ok",
      value: "正常",
      detail: "最近心跳 相对(2026-11-04T10:00:00Z)",
      title: "完整(2026-11-04T10:00:00Z)",
    });
  });

  // covers: spec 0011 AC-22
  it("任务无响应时标红", () => {
    const line = describeMediaWorker({ ...MEDIA, stale: true }, time);
    expect(line.tone).toBe("error");
    expect(line.value).toBe("附件下载任务无响应");
  });

  it("从没有心跳且无响应时说明还没有心跳", () => {
    expect(describeMediaWorker({ ...MEDIA, stale: true, last_seen_at: null }, time).detail).toBe(
      "还没有心跳",
    );
  });

  // covers: spec 0011 AC-22
  it("403 熔断暂停时显示暂停到什么时候", () => {
    const line = describeMediaWorker({ ...MEDIA, paused_until: "2026-11-04T10:30:00Z" }, time);
    expect(line.tone).toBe("pending");
    expect(line.value).toBe("已暂停到 完整(2026-11-04T10:30:00Z)");
  });

  it("没有心跳也没有无响应时是未运行", () => {
    expect(describeMediaWorker({ ...MEDIA, last_seen_at: null }, time).value).toBe("未运行");
  });
});

const HISTORY: SystemStatusData["history"] = {
  paused_reason: null,
  paused_at: null,
  waiting_reason: null,
  waiting_until: null,
  pages_used_24h: 0,
  daily_page_limit: 1500,
  quota_resumes_at: null,
  last_seen_at: "2026-11-05T10:00:00Z",
  stale: false,
};

describe("describeHistoryWorker", () => {
  // covers: spec 0012 AC-20
  it("正常时显示最近心跳的相对时间", () => {
    expect(describeHistoryWorker(HISTORY, time)).toEqual({
      tone: "ok",
      value: "正常",
      detail: "最近心跳 相对(2026-11-05T10:00:00Z)",
      title: "完整(2026-11-05T10:00:00Z)",
    });
  });

  // covers: spec 0012 AC-20
  it("进程心跳正常而任务心跳超时，用 error 色调说无响应", () => {
    const line = describeHistoryWorker({ ...HISTORY, stale: true }, time);
    expect(line.tone).toBe("error");
    expect(line.value).toBe("历史采集任务无响应");
    expect(
      describeHistoryWorker({ ...HISTORY, stale: true, last_seen_at: null }, time).detail,
    ).toBe("还没有心跳");
  });

  // covers: spec 0012 AC-20
  it("总开关暂停时显示暂停原因，自动暂停标红", () => {
    const manual = describeHistoryWorker({ ...HISTORY, paused_reason: "manual" }, time);
    expect(manual).toEqual({ tone: "pending", value: "你手动暂停" });
    const limited = describeHistoryWorker({ ...HISTORY, paused_reason: "rate_limited" }, time);
    expect(limited.tone).toBe("error");
    expect(limited.value).toBe("因限速自动暂停");
  });

  // covers: spec 0012 AC-20
  it("休息中和等限额时显示继续的本地时间", () => {
    const rest = describeHistoryWorker(
      { ...HISTORY, waiting_reason: "rest", waiting_until: "2026-11-05T10:05:00Z" },
      time,
    );
    expect(rest.value).toBe("休息中，完整(2026-11-05T10:05:00Z) 继续");
    const quota = describeHistoryWorker(
      { ...HISTORY, waiting_reason: "quota", waiting_until: "2026-11-06T01:00:00Z" },
      time,
    );
    expect(quota.value).toBe("今日额度已用完，最早 完整(2026-11-06T01:00:00Z) 继续");
  });

  it("没有心跳也没有无响应时是未运行", () => {
    expect(describeHistoryWorker({ ...HISTORY, last_seen_at: null }, time).value).toBe("未运行");
  });
});
