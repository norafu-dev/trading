// @vitest-environment node
import { beforeAll, describe, expect, it } from "vitest";

import { initParser, type MarkdownNode, parseMarkdown } from "./parser";

// 收集语法树里出现的所有节点类型（含提及和表情的子类型，例如 mention:user）
function collectTypes(value: unknown, into: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) collectTypes(item, into);
    return into;
  }
  if (value && typeof value === "object") {
    const node = value as {
      type?: unknown;
      value?: unknown;
      content?: unknown;
      items?: unknown;
      text?: unknown;
    };
    if (typeof node.type === "string") into.add(node.type);
    const inner = node.value as { type?: unknown } | undefined;
    if ((node.type === "mention" || node.type === "emoji") && typeof inner?.type === "string") {
      into.add(`${node.type}:${inner.type}`);
    }
    for (const child of [node.value, node.content, node.items, node.text])
      collectTypes(child, into);
  }
  return into;
}

const SAMPLE = [
  "# 标题",
  "**粗** *斜* __下__ ~~删~~ `行内` ||剧透||",
  "> 引用",
  "- 列表项",
  "[文字链接](https://example.com) https://example.org",
  "<@123> <@&456> <#789> @everyone @here",
  "<:pepe:111> <a:wave:222> <t:1793340332:F>",
  "```ts",
  "const x = 1;",
  "```",
].join("\n");

describe("parseMarkdown（真实 WASM 解析器）", () => {
  beforeAll(async () => {
    await initParser();
  });

  // covers: AC-9
  it("一条包含全部语法的消息解析出预期的节点类型", () => {
    const result = parseMarkdown(SAMPLE);
    expect(result.ok).toBe(true);
    const types = collectTypes(result.ok ? (result.blocks as readonly MarkdownNode[]) : []);
    for (const expected of [
      "heading",
      "bold",
      "italic",
      "underline",
      "strikethrough",
      "code",
      "spoiler",
      "quote",
      "list",
      "link",
      "mention:user",
      "mention:role",
      "mention:channel",
      "mention:everyone",
      "mention:here",
      "emoji:custom",
      "timestamp",
      "code_block",
    ]) {
      expect(types, `缺少 ${expected}`).toContain(expected);
    }
  });

  it("重复初始化不会报错", async () => {
    await expect(initParser()).resolves.toBeUndefined();
  });

  it("纯文字解析成段落", () => {
    const result = parseMarkdown("你好");
    expect(result).toMatchObject({ ok: true, blocks: [{ type: "paragraph" }] });
  });
});
