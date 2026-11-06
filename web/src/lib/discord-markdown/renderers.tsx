"use client";

import { cn } from "cn";
import { Fragment, type KeyboardEvent, type ReactNode, useState } from "react";

import type { MentionResolver } from "@/components/messages/types";
import { HighlightedText } from "@/components/search/highlighted-text";
import { emojiUrl } from "@/lib/discord-cdn";
import type { TimeFormat } from "@/lib/format/time";
import { hostDiffers, looksLikeUrl, safeUrl } from "@/lib/safe-url";

import type { MarkdownNode, MarkdownNodeType, MentionValue } from "./parser";

export type RenderContext = Readonly<{
  resolver: MentionResolver;
  now: Date;
  format: TimeFormat;
  /** 已经显示出来的剧透 id；拆到多个块里的同一个剧透共用一个 id，一起显示。 */
  revealed: ReadonlySet<number>;
  reveal: (id: number) => void;
  /** 搜索命中词，只在普通文字节点里高亮（spec 0013 AC-13）。 */
  highlightTerms?: readonly string[];
}>;

type NodeOf<T extends MarkdownNodeType> = Extract<MarkdownNode, { type: T }>;
type Renderer<T extends MarkdownNodeType> = (node: NodeOf<T>, ctx: RenderContext) => ReactNode;
type RendererTable = { readonly [T in MarkdownNodeType]: Renderer<T> };

const MENTION_CLASS = "rounded-sm bg-mention px-0.5 font-medium";

// 直接调用渲染函数（不包成组件），整棵树同步建好，调用方的 try/catch 能接住任何节点的错误。
export function renderNodes(nodes: readonly MarkdownNode[], ctx: RenderContext): ReactNode[] {
  return nodes.map((node, index) => (
    // biome-ignore lint/suspicious/noArrayIndexKey: 同一条消息的语法树不会重排
    <Fragment key={index}>{renderNode(node, ctx)}</Fragment>
  ));
}

// 运行时遇到类型表里没有的节点（库升级后新加的语法），按它的文字内容显示。
function textOf(node: unknown): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object") {
    const value = (node as { value?: unknown; content?: unknown }).value;
    if (typeof value === "string") return value;
    if (value !== undefined) return textOf(value);
    return textOf((node as { content?: unknown }).content ?? "");
  }
  return "";
}

function renderNode(node: MarkdownNode, ctx: RenderContext): ReactNode {
  const renderer = RENDERERS[node.type] as Renderer<MarkdownNodeType> | undefined;
  return renderer ? renderer(node as NodeOf<MarkdownNodeType>, ctx) : textOf(node);
}

function Spoiler({
  id,
  children,
  ctx,
}: Readonly<{ id: number; children: ReactNode; ctx: RenderContext }>) {
  const shown = ctx.revealed.has(id);
  const onKeyDown = (event: KeyboardEvent<HTMLSpanElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      ctx.reveal(id);
    }
  };
  return (
    // biome-ignore lint/a11y/useSemanticElements: 剧透里可能有链接，<button> 里不能放可交互内容
    <span
      role="button"
      tabIndex={shown ? -1 : 0}
      aria-expanded={shown}
      aria-label={shown ? undefined : "剧透，按回车显示"}
      onClick={() => ctx.reveal(id)}
      onKeyDown={onKeyDown}
      className={cn(
        "rounded-sm px-0.5 transition-colors",
        shown ? "bg-secondary" : "cursor-pointer bg-foreground text-transparent select-none",
      )}
    >
      <span aria-hidden={!shown}>{children}</span>
    </span>
  );
}

function CustomEmoji({
  id,
  name,
  animated,
}: Readonly<{ id: string; name: string; animated: boolean }>) {
  const [failed, setFailed] = useState(false);
  if (failed) return <span>:{name}:</span>;
  return (
    // biome-ignore lint/performance/noImgElement: 静态导出不做图片优化，表情直接从 Discord CDN 加载
    <img
      src={emojiUrl(id, animated)}
      alt={`:${name}:`}
      title={`:${name}:`}
      loading="lazy"
      onError={() => setFailed(true)}
      className="inline-block size-[1.375em] object-contain align-[-0.3em]"
    />
  );
}

function renderLink(node: NodeOf<"link">, ctx: RenderContext): ReactNode {
  const { text, url: raw } = node.value;
  const shownText = text ? textOf(text) : raw;
  const children = text ? renderNodes(text, ctx) : raw;
  const url = safeUrl(raw);
  if (!url) return <span>{children}</span>;
  const phishing = text !== undefined && looksLikeUrl(shownText) && hostDiffers(shownText, url);
  return (
    <>
      <a
        href={url.href}
        target="_blank"
        rel="noopener noreferrer nofollow"
        title={url.href}
        className="text-link underline-offset-2 hover:underline"
      >
        {children}
      </a>
      {phishing ? (
        <span className="ml-1 text-xs text-muted-foreground">（实际指向 {url.hostname}）</span>
      ) : null}
    </>
  );
}

// @用户、@身份组、@everyone、@here 不显示（spec 0015）：正文在解析前已经去掉，这里兜底返回 null。
function mentionText(mention: MentionValue, resolver: MentionResolver): string | null {
  switch (mention.type) {
    case "user":
    case "role":
    case "everyone":
    case "here":
      return null;
    case "channel": {
      const name = resolver.channel(String(mention.value));
      return name ? `#${name}` : "#未知频道";
    }
    case "command": {
      const value = mention.value as { name?: unknown } | undefined;
      return typeof value?.name === "string" ? `/${value.name}` : "/命令";
    }
    default:
      return "@提及";
  }
}

const HEADING_CLASSES: Readonly<Record<number, string>> = {
  1: "text-xl font-bold",
  2: "text-base font-bold",
  3: "text-sm font-bold",
};

// 每种语法节点对应一个渲染函数；类型表保证解析器类型里的每一种节点都有（穷举检查）。
const RENDERERS = {
  text: (node, ctx) =>
    ctx.highlightTerms?.length ? (
      <HighlightedText text={node.value} terms={ctx.highlightTerms} />
    ) : (
      node.value
    ),
  empty: () => (
    <div aria-hidden>
      <br />
    </div>
  ),
  paragraph: (node, ctx) => <div>{renderNodes(node.value, ctx)}</div>,
  heading: (node, ctx) => (
    <div className={cn("mt-1", HEADING_CLASSES[node.value.level] ?? HEADING_CLASSES[3])}>
      {renderNodes(node.value.content, ctx)}
    </div>
  ),
  small: (node, ctx) => (
    <div className="text-xs text-muted-foreground">{renderNodes(node.value.content, ctx)}</div>
  ),
  quote: (node, ctx) => (
    <blockquote className="my-0.5 border-l-4 border-border pl-3">
      {renderNodes(node.value, ctx)}
    </blockquote>
  ),
  list: (node, ctx) => {
    const items = node.value.items.map((item, index) => (
      // biome-ignore lint/suspicious/noArrayIndexKey: 同一条消息的语法树不会重排
      <li key={index}>{renderNodes(item.content, ctx)}</li>
    ));
    return node.value.type === "ordered" ? (
      <ol start={node.value.value} className="list-decimal pl-6">
        {items}
      </ol>
    ) : (
      <ul className="list-disc pl-6">{items}</ul>
    );
  },
  bold: (node, ctx) => <strong className="font-semibold">{renderNodes(node.value, ctx)}</strong>,
  italic: (node, ctx) => <em>{renderNodes(node.value, ctx)}</em>,
  underline: (node, ctx) => <u className="underline-offset-2">{renderNodes(node.value, ctx)}</u>,
  strikethrough: (node, ctx) => <s>{renderNodes(node.value, ctx)}</s>,
  spoiler: (node, ctx) => (
    <Spoiler id={node.value.id} ctx={ctx}>
      {renderNodes(node.value.content, ctx)}
    </Spoiler>
  ),
  code: (node) => (
    <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-sm">{node.value}</code>
  ),
  code_block: (node) => (
    <div className="relative my-1 max-w-full">
      {node.value.language ? (
        <span className="absolute top-1.5 right-2 text-xs text-muted-foreground">
          {node.value.language}
        </span>
      ) : null}
      <pre className="max-h-[400px] overflow-auto rounded-md border border-border bg-muted p-3 font-mono text-sm whitespace-pre">
        <code>{node.value.content}</code>
      </pre>
    </div>
  ),
  link: renderLink,
  mention: (node, ctx) => {
    const text = mentionText(node.value, ctx.resolver);
    return text === null ? null : <span className={MENTION_CLASS}>{text}</span>;
  },
  emoji: (node) =>
    node.value.type === "custom" ? (
      <CustomEmoji
        id={String(node.value.value.id)}
        name={node.value.value.name}
        animated={node.value.value.animated}
      />
    ) : (
      node.value.value
    ),
  timestamp: (node, ctx) => {
    const seconds = String(node.value.value);
    const style = node.value.style;
    const shown = ctx.format.discordTimestamp(seconds, style, ctx.now);
    if (shown === null) return `<t:${seconds}${style ? `:${style}` : ""}>`;
    const iso = new Date(Number(seconds) * 1000).toISOString();
    return (
      <time dateTime={iso} title={ctx.format.fullTime(iso)} className="rounded-sm bg-muted px-1">
        {shown}
      </time>
    );
  },
} satisfies RendererTable;
