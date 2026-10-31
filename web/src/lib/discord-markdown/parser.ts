// 唯一引入 @discord/markdown-wasm 的模块（spec 0003）。其他地方只用这里带类型的
// `parseMarkdown` 和下面的节点类型。

import type {
  ChannelMention,
  Code,
  CodeBlock,
  CustomEmoji,
  Empty,
  EveryoneMention,
  HereMention,
  RoleMention,
  Text,
  Timestamp,
  UnicodeEmoji,
  UserMention,
} from "@discord/markdown-types";
import { init, parse } from "@discord/markdown-wasm";

// 官方类型的 Block 和 Inline 联合里漏了 `paragraph` 和 `text`，但解析器实际会输出这两种，
// 所以在这里重新声明一遍语法树，把它们补上。
export type InlineNode =
  | Text
  | Code
  | CodeBlock
  | Readonly<{ type: "emoji"; value: CustomEmoji | UnicodeEmoji }>
  | Readonly<{
      type: "link";
      value: Readonly<{ text?: readonly InlineNode[]; url: string; title?: string }>;
    }>
  | Readonly<{ type: "mention"; value: MentionValue }>
  | Readonly<{ type: "bold"; value: readonly InlineNode[] }>
  | Readonly<{ type: "italic"; value: readonly InlineNode[] }>
  | Readonly<{ type: "underline"; value: readonly InlineNode[] }>
  | Readonly<{ type: "strikethrough"; value: readonly InlineNode[] }>
  | Readonly<{ type: "spoiler"; value: Readonly<{ id: number; content: readonly InlineNode[] }> }>
  | Timestamp;

export type MentionValue =
  | UserMention
  | ChannelMention
  | RoleMention
  | EveryoneMention
  | HereMention
  | Readonly<{ type: "game" | "command" | "static"; value?: unknown }>;

export type ListItemNode = Readonly<{ content: readonly BlockNode[] }>;

export type BlockNode =
  | Empty
  | Readonly<{ type: "paragraph"; value: readonly InlineNode[] }>
  | Readonly<{
      type: "heading";
      value: Readonly<{ level: number; content: readonly InlineNode[] }>;
    }>
  | Readonly<{
      type: "list";
      value: Readonly<{
        type: "unordered" | "ordered";
        value?: number;
        items: readonly ListItemNode[];
      }>;
    }>
  | Readonly<{ type: "quote"; value: readonly BlockNode[] }>
  | Readonly<{ type: "small"; value: Readonly<{ content: readonly InlineNode[] }> }>;

export type MarkdownNode = InlineNode | BlockNode;
export type MarkdownNodeType = MarkdownNode["type"];

export type ParseResult =
  | Readonly<{ ok: true; blocks: readonly BlockNode[] }>
  | Readonly<{ ok: false; error: unknown }>;

// 对「模块级变量只能是常量」规则的有意例外（spec 0003）：缓存 WASM 初始化的 promise，
// 保证整个页面只初始化一次。只在第一次调用时赋值（失败时清空以便重试）。
let initPromise: Promise<void> | null = null;

export function initParser(): Promise<void> {
  initPromise ??= init().catch((error: unknown) => {
    initPromise = null;
    throw error;
  });
  return initPromise;
}

export function parseMarkdown(content: string): ParseResult {
  try {
    return { ok: true, blocks: parse(content) as unknown as readonly BlockNode[] };
  } catch (error) {
    return { ok: false, error };
  }
}
