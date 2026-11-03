import { describe, expect, it } from "vitest";

import {
  createTimeFormat,
  formatDayDivider,
  formatDiscordTimestamp,
  formatFullTime,
  formatHoverTime,
  formatMessageTime,
} from "./time";

const SHANGHAI = "Asia/Shanghai";
const LOS_ANGELES = "America/Los_Angeles";

// 上海时间 2026-10-30 14:05:32（星期五）
const FRIDAY_AFTERNOON = "2026-10-30T06:05:32Z";
const secondsOf = (iso: string) => String(Date.parse(iso) / 1000);

describe("formatMessageTime", () => {
  // covers: AC-6
  const now = new Date("2026-10-31T10:00:00Z"); // 上海 10月31日 18:00

  it("今天的消息只显示 HH:mm", () => {
    expect(formatMessageTime("2026-10-31T06:05:00Z", now, SHANGHAI)).toBe("14:05");
  });

  it("昨天的消息显示「昨天 HH:mm」", () => {
    expect(formatMessageTime(FRIDAY_AFTERNOON, now, SHANGHAI)).toBe("昨天 14:05");
  });

  it("今年更早的消息显示月日和时间", () => {
    expect(formatMessageTime("2026-10-29T06:05:00Z", now, SHANGHAI)).toBe("10月29日 14:05");
  });

  it("往年的消息带年份", () => {
    expect(formatMessageTime("2025-10-30T06:05:00Z", now, SHANGHAI)).toBe("2025年10月30日 14:05");
  });

  it("按本地日历日判断今天，而不是按相差 24 小时", () => {
    // 上海 10月31日 00:05，离 now 不到 18 小时，但已经是今天
    expect(formatMessageTime("2026-10-30T16:05:00Z", now, SHANGHAI)).toBe("00:05");
    // 上海 10月30日 23:55，离 now 只有 18 小时多一点，仍是昨天
    expect(formatMessageTime("2026-10-30T15:55:00Z", now, SHANGHAI)).toBe("昨天 23:55");
  });

  it("1 月 1 日时，去年 12 月 31 日算昨天", () => {
    const newYear = new Date("2026-01-01T02:00:00Z"); // 上海 1月1日 10:00
    expect(formatMessageTime("2025-12-31T06:05:00Z", newYear, SHANGHAI)).toBe("昨天 14:05");
  });

  it("1 月 1 日时，去年 12 月 30 日按往年显示", () => {
    const newYear = new Date("2026-01-01T02:00:00Z");
    expect(formatMessageTime("2025-12-30T06:05:00Z", newYear, SHANGHAI)).toBe(
      "2025年12月30日 14:05",
    );
  });

  it("美国夏令时结束当天（多出一小时），前一天凌晨的消息仍是昨天", () => {
    // 洛杉矶 2026-11-01 20:00 PST；消息是 10月31日 00:30 PDT，相差 44.5 小时
    const now = new Date("2026-11-02T04:00:00Z");
    expect(formatMessageTime("2026-10-31T07:30:00Z", now, LOS_ANGELES)).toBe("昨天 00:30");
  });

  it("美国夏令时开始当天（少一小时），不到 24 小时前的前一天消息仍是昨天", () => {
    // 洛杉矶 2026-03-09 00:30 PDT；消息是 3月8日 00:10 PST，只相差 23 小时 20 分
    const now = new Date("2026-03-09T07:30:00Z");
    expect(formatMessageTime("2026-03-08T08:10:00Z", now, LOS_ANGELES)).toBe("昨天 00:10");
  });

  it("同一个时刻在不同时区显示不同的本地时间", () => {
    const iso = "2026-10-30T14:30:00Z";
    const at = new Date("2026-10-30T14:40:00Z");
    expect(formatMessageTime(iso, at, "Asia/Tokyo")).toBe("23:30");
    expect(formatMessageTime(iso, at, LOS_ANGELES)).toBe("07:30");
  });
});

describe("formatHoverTime 和 formatFullTime", () => {
  // covers: AC-5, AC-6
  it("悬停时间是 24 小时制的 HH:mm", () => {
    expect(formatHoverTime("2026-10-30T13:07:00Z", SHANGHAI)).toBe("21:07");
  });

  it("完整时间带日期、星期和秒", () => {
    expect(formatFullTime(FRIDAY_AFTERNOON, SHANGHAI)).toBe("2026年10月30日 星期五 14:05:32");
  });
});

describe("formatDayDivider 和 dayKey", () => {
  // covers: AC-5
  it("分隔线文字不补零", () => {
    expect(formatDayDivider("2026-01-05")).toBe("2026年1月5日");
  });

  it("dayKey 按时区取本地日历日并补零", () => {
    const date = new Date("2026-01-04T20:00:00Z");
    expect(createTimeFormat("Asia/Tokyo").dayKey(date)).toBe("2026-01-05");
    expect(createTimeFormat(LOS_ANGELES).dayKey(date)).toBe("2026-01-04");
  });
});

describe("formatDiscordTimestamp", () => {
  // covers: AC-9
  const seconds = secondsOf(FRIDAY_AFTERNOON);
  const now = new Date(FRIDAY_AFTERNOON);

  it.each([
    ["t", "14:05"],
    ["T", "14:05:32"],
    ["d", "2026/10/30"],
    ["D", "2026年10月30日"],
    ["f", "2026年10月30日 14:05"],
    ["F", "2026年10月30日 星期五 14:05"],
  ])("格式字母 %s 显示为 %s", (style, expected) => {
    expect(formatDiscordTimestamp(seconds, style, now, SHANGHAI)).toBe(expected);
  });

  it("省略格式字母时按 f 显示", () => {
    expect(formatDiscordTimestamp(seconds, undefined, now, SHANGHAI)).toBe("2026年10月30日 14:05");
  });

  it("R 显示过去的相对时间", () => {
    const later = new Date(Date.parse(FRIDAY_AFTERNOON) + 3 * 3600 * 1000);
    expect(formatDiscordTimestamp(seconds, "R", later, SHANGHAI)).toBe("3小时前");
  });

  it("R 显示将来的相对时间", () => {
    const earlier = new Date(Date.parse(FRIDAY_AFTERNOON) - 5 * 60 * 1000);
    expect(formatDiscordTimestamp(seconds, "R", earlier, SHANGHAI)).toBe("5分钟后");
  });

  it("R 随着 now 变化而更新", () => {
    const first = formatDiscordTimestamp(
      seconds,
      "R",
      new Date(Date.parse(FRIDAY_AFTERNOON) + 60_000),
      SHANGHAI,
    );
    const second = formatDiscordTimestamp(
      seconds,
      "R",
      new Date(Date.parse(FRIDAY_AFTERNOON) + 120_000),
      SHANGHAI,
    );
    expect(first).not.toBe(second);
  });

  it.each([
    ["不是数字的时间戳", "abc", "f"],
    ["带小数点的时间戳", "1.5", "f"],
    ["超出 Date 范围的时间戳", "99999999999999999", "f"],
    ["不认识的格式字母", seconds, "x"],
  ])("%s返回 null", (_label, value, style) => {
    expect(formatDiscordTimestamp(value, style, now, SHANGHAI)).toBeNull();
  });
});
