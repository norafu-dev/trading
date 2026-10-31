// The only module that imports @discord/markdown-wasm (spec 0003). Everything else uses the
// typed `parseMarkdown` and the node types below.

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

// The published types leave `paragraph` and `text` out of the Block and Inline unions although the
// parser emits both, so the tree is redeclared here with them included.
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

// Deliberate exception to "module level values are constants" (spec 0003): the one time WASM init
// promise, cached so the whole page instantiates the module once. Assigned only on first call.
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
