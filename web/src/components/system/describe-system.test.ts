import { describe, expect, it } from "vitest";

import { describeMediaWorker, type TimeText } from "./describe-system";
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
