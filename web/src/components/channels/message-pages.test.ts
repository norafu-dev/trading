import { describe, expect, it } from "vitest";

import {
  chunk,
  flatten,
  hasGap,
  type MessagesData,
  type MessagesPage,
  mergeIntoPages,
  pageFrom,
  planChanged,
} from "./message-pages";

type Row = MessagesPage["messages"][number];

const row = (id: string, content = "c"): Row => ({
  id,
  channel_id: "1",
  author_id: "2",
  author_name: "a",
  author_avatar_hash: null,
  media: [],
  created_at: "2026-11-02T00:00:00Z",
  content,
  embeds: [],
  forwarded: null,
  attachments: [],
  edited_at: null,
  deleted_at: null,
  reply: null,
});

const page = (ids: readonly string[], overrides: Partial<MessagesPage> = {}): MessagesPage => ({
  messages: ids.map((id) => row(id)),
  has_more: false,
  users: [],
  channels: [],
  ...overrides,
});

const twoPages: MessagesData = {
  pages: [page(["30", "40"]), page(["10", "20"])],
  pageParams: [null, "30"],
};

const idsOf = (data: MessagesData) => flatten(data).map((m) => m.id);

describe("mergeIntoPages", () => {
  // covers: spec 0008 AC-17
  it("新消息接到末尾，同一条不会出现两次", () => {
    const merged = mergeIntoPages(twoPages, page(["40", "50"]));
    expect(idsOf(merged)).toEqual(["10", "20", "30", "40", "50"]);
  });

  // covers: spec 0008 AC-18
  it("旧页里的消息就地更新，不搬到最新一页", () => {
    const merged = mergeIntoPages(twoPages, { ...page([]), messages: [row("10", "edited")] });
    expect(idsOf(merged)).toEqual(["10", "20", "30", "40"]);
    expect(flatten(merged)[0]?.content).toBe("edited");
  });

  // covers: spec 0008 AC-21
  it("提及的名字合并，不丢旧的", () => {
    const base = pageFrom(page(["1"], { users: [{ id: "u1", name: "甲" }] }));
    const merged = mergeIntoPages(base, page(["2"], { users: [{ id: "u2", name: "乙" }] }));
    expect(merged.pages[0]?.users.map((u) => u.name)).toEqual(["甲", "乙"]);
  });
});

describe("hasGap", () => {
  // covers: spec 0008 AC-19
  it("最新一页全部比已有的新才算有缺口", () => {
    expect(hasGap(twoPages, page(["40", "50"]))).toBe(false);
    expect(hasGap(twoPages, page(["50", "60"]))).toBe(true);
    expect(hasGap(twoPages, page([]))).toBe(false);
  });
});

describe("planChanged", () => {
  // covers: spec 0008 AC-18
  it("范围内就地更新，更新的触发 after，更旧的忽略", () => {
    expect(planChanged(twoPages, ["5", "20", "40", "99"])).toEqual({
      fetchIds: ["20", "40"],
      needsAfter: true,
    });
    expect(planChanged(twoPages, ["5"])).toEqual({ fetchIds: [], needsAfter: false });
  });

  it("列表是空的时只取最新", () => {
    expect(planChanged(pageFrom(page([])), ["5"])).toEqual({ fetchIds: [], needsAfter: true });
  });
});

describe("chunk", () => {
  it("按大小切开", () => {
    expect(chunk([1, 2, 3], 2)).toEqual([[1, 2], [3]]);
  });
});
