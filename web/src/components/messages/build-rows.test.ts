import { describe, expect, it } from "vitest";

import { buildRows, countNewMessages, maxMessageId, type Row } from "./build-rows";
import type { MessageView } from "./types";

const SHANGHAI = "Asia/Shanghai";

function message(id: string, createdAt: string, overrides: Partial<MessageView> = {}): MessageView {
  return {
    id,
    authorId: "100",
    authorName: "阿青",
    authorAvatarUrl: null,
    createdAt,
    content: `消息 ${id}`,
    embeds: [],
    forwarded: null,
    attachments: [],
    editedAt: null,
    deleted: false,
    ...overrides,
  };
}

const at = (base: string, offsetMs: number) => new Date(Date.parse(base) + offsetMs).toISOString();
const BASE = "2026-10-30T06:00:00Z"; // 上海 14:00

// 只看消息行是否开新组，按顺序返回 [id, groupStart]
function groupStarts(rows: readonly Row[]): [string, boolean][] {
  return rows.flatMap((row) => (row.kind === "message" ? [[row.key, row.groupStart]] : []));
}

const dayRows = (rows: readonly Row[]) => rows.filter((row) => row.kind === "day");

describe("buildRows 分组", () => {
  // covers: AC-5
  it("同一作者 6 分 59 秒内的消息合并成一组", () => {
    const rows = buildRows(
      [message("1", BASE), message("2", at(BASE, 6 * 60_000 + 59_000))],
      SHANGHAI,
    );
    expect(groupStarts(rows)).toEqual([
      ["1", true],
      ["2", false],
    ]);
  });

  it("间隔正好 7 分钟另起一组", () => {
    const rows = buildRows([message("1", BASE), message("2", at(BASE, 7 * 60_000))], SHANGHAI);
    expect(groupStarts(rows)).toEqual([
      ["1", true],
      ["2", true],
    ]);
  });

  it("换作者另起一组", () => {
    const rows = buildRows(
      [message("1", BASE), message("2", at(BASE, 1000), { authorId: "200", authorName: "小白" })],
      SHANGHAI,
    );
    expect(groupStarts(rows)[1]).toEqual(["2", true]);
  });

  it("同一个 webhook 换名字发言另起一组", () => {
    const rows = buildRows(
      [
        message("1", BASE, { authorId: "900", authorName: "GitHub" }),
        message("2", at(BASE, 1000), { authorId: "900", authorName: "RSS 订阅" }),
      ],
      SHANGHAI,
    );
    expect(groupStarts(rows)[1]).toEqual(["2", true]);
  });

  it("中间夹一条已删除的消息不打断分组", () => {
    const rows = buildRows(
      [
        message("1", BASE),
        message("2", at(BASE, 60_000), { deleted: true }),
        message("3", at(BASE, 120_000)),
      ],
      SHANGHAI,
    );
    expect(groupStarts(rows)).toEqual([
      ["1", true],
      ["2", false],
      ["3", false],
    ]);
  });

  it("跨过本地午夜插入日期分隔线，并另起一组", () => {
    // 上海 23:58 和次日 00:01，只差 3 分钟
    const rows = buildRows(
      [message("1", "2026-10-30T15:58:00Z"), message("2", "2026-10-30T16:01:00Z")],
      SHANGHAI,
    );
    expect(rows.map((row) => row.key)).toEqual(["day:2026-10-30", "1", "day:2026-10-31", "2"]);
    expect(groupStarts(rows)[1]).toEqual(["2", true]);
  });

  it("日期分隔线按传入的时区划分，东京和洛杉矶结果不同", () => {
    // 东京 23:58 / 00:01（跨日）；洛杉矶 07:58 / 08:01（同一天）
    const messages = [message("1", "2026-10-30T14:58:00Z"), message("2", "2026-10-30T15:01:00Z")];
    const tokyo = buildRows(messages, "Asia/Tokyo");
    const losAngeles = buildRows(messages, "America/Los_Angeles");
    expect(dayRows(tokyo)).toHaveLength(2);
    expect(dayRows(losAngeles)).toHaveLength(1);
    expect(groupStarts(losAngeles)[1]).toEqual(["2", false]);
  });

  it("空列表没有任何行", () => {
    expect(buildRows([], SHANGHAI)).toEqual([]);
  });
});

describe("buildRows 的行 key", () => {
  // covers: AC-8
  it("在前面插入 500 条旧消息后，原有消息的行 key 和顺序不变", () => {
    const original = [message("10000", BASE), message("10001", at(BASE, 60_000))];
    const older = Array.from({ length: 500 }, (_, index) =>
      message(String(9000 + index), at(BASE, -(500 - index) * 1000)),
    );

    const before = buildRows(original, SHANGHAI).map((row) => row.key);
    const after = buildRows([...older, ...original], SHANGHAI).map((row) => row.key);

    expect(after.slice(-2)).toEqual(before.slice(-2));
    expect(after).toContain("day:2026-10-30");
    expect(new Set(after).size).toBe(after.length);
  });
});

describe("countNewMessages", () => {
  // covers: AC-7
  const seen = [message("100", BASE), message("101", at(BASE, 1000))];

  it("末尾追加的消息计入新消息", () => {
    const next = [...seen, message("102", at(BASE, 2000)), message("103", at(BASE, 3000))];
    expect(countNewMessages(next, maxMessageId(seen))).toBe(2);
  });

  it("在前面插入旧消息不计数", () => {
    const next = [message("50", at(BASE, -1000)), ...seen];
    expect(countNewMessages(next, maxMessageId(seen))).toBe(0);
  });

  it("编辑或删除已有消息不计数", () => {
    const next = [
      seen[0] as MessageView,
      { ...(seen[1] as MessageView), content: "改过了", deleted: true },
    ];
    expect(countNewMessages(next, maxMessageId(seen))).toBe(0);
  });

  it("按雪花 ID 的数值比较，不按字符串比较", () => {
    // 字符串比较时 "99" > "100"，数值比较时不是
    const next = [message("99", BASE), message("100", at(BASE, 1000))];
    expect(countNewMessages(next, "99")).toBe(1);
  });

  it("超过 JS 安全整数的 ID 也能正确比较", () => {
    const next = [
      message("1234567890123456789", BASE),
      message("1234567890123456790", at(BASE, 1)),
    ];
    expect(countNewMessages(next, "1234567890123456789")).toBe(1);
  });

  it("还没记录过看到的最大 ID 时是 0", () => {
    expect(countNewMessages(seen, null)).toBe(0);
  });
});

describe("maxMessageId", () => {
  it("返回最后一条消息的 ID，空列表返回 null", () => {
    expect(maxMessageId([message("1", BASE), message("2", BASE)])).toBe("2");
    expect(maxMessageId([])).toBeNull();
  });
});
