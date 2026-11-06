import { describe, expect, it } from "vitest";

import { splitHighlights } from "./highlight";

const hits = (text: string, terms: string[]) =>
  splitHighlights(text, terms)
    .filter((p) => p.hit)
    .map((p) => p.text);

describe("splitHighlights", () => {
  it("没有词时整段不高亮", () => {
    expect(splitHighlights("hello", [])).toEqual([{ text: "hello", hit: false }]);
  });

  it("不分大小写，保留原文的大小写", () => {
    expect(hits("BTCUSDT 和 btc", ["btc"])).toEqual(["BTC", "btc"]);
  });

  it("短语里的空格匹配任意长度的空白", () => {
    expect(hits("止损   位 很重要", ["止损 位"])).toEqual(["止损   位"]);
  });

  it("特殊字符当普通字符", () => {
    expect(hits("涨了 50% (大)", ["50%", "(大)"])).toEqual(["50%", "(大)"]);
  });

  it("重叠的词合并成一段", () => {
    expect(splitHighlights("abcdef", ["abcd", "cdef"])).toEqual([{ text: "abcdef", hit: true }]);
  });

  it("被 markdown 拆开的词不在同一个文字节点里，调用方传入的片段各自不命中", () => {
    expect(hits("B", ["btc"])).toEqual([]);
    expect(hits("TC", ["btc"])).toEqual([]);
  });

  it("转小写会改变长度的字符也不会切错位置", () => {
    expect(splitHighlights("İstanbul btc", ["btc"])).toEqual([
      { text: "İstanbul ", hit: false },
      { text: "btc", hit: true },
    ]);
  });
});
