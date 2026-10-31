"use client";

import { type ReactNode, useCallback, useMemo, useState } from "react";

import type { MentionResolver } from "@/components/messages/types";
import type { TimeFormat } from "@/lib/format/time";

import { parseMarkdown } from "./parser";
import { type RenderContext, renderNodes } from "./renderers";
import type { ParserStatus } from "./use-markdown-parser";

type MessageContentProps = Readonly<{
  content: string;
  parser: ParserStatus;
  resolver: MentionResolver;
  now: Date;
  format: TimeFormat;
  /** 跟在正文末尾的内容，例如「（已编辑）」。 */
  suffix?: ReactNode;
}>;

function PlainText({ content, suffix }: Readonly<{ content: string; suffix?: ReactNode }>) {
  return (
    <div className="whitespace-pre-wrap">
      {content}
      {suffix}
    </div>
  );
}

// 把一条消息的 Discord markdown 变成 React 元素（从不当 HTML 插入）。解析器加载失败时
// 显示纯文本；单条消息解析或渲染出错时，只有这一条显示原始文字（AC-10）。
export function MessageContent({
  content,
  parser,
  resolver,
  now,
  format,
  suffix,
}: MessageContentProps) {
  const [revealed, setRevealed] = useState<ReadonlySet<number>>(() => new Set());
  const reveal = useCallback(
    (id: number) => setRevealed((current) => new Set([...current, id])),
    [],
  );
  const parsed = useMemo(
    () => (parser === "ready" && content ? parseMarkdown(content) : null),
    [parser, content],
  );

  if (!content) return suffix ? <div>{suffix}</div> : null;
  if (parsed === null || !parsed.ok) return <PlainText content={content} suffix={suffix} />;

  const ctx: RenderContext = { resolver, now, format, revealed, reveal };
  // 最后一块是段落时，把 suffix 接在它的末尾，和正文在同一行。
  const blocks = parsed.blocks;
  const last = blocks.at(-1);
  let rendered: ReactNode;
  try {
    rendered =
      last?.type === "paragraph" ? (
        <>
          {renderNodes(blocks.slice(0, -1), ctx)}
          <div>
            {renderNodes(last.value, ctx)}
            {suffix}
          </div>
        </>
      ) : (
        <>
          {renderNodes(blocks, ctx)}
          {suffix ? <div>{suffix}</div> : null}
        </>
      );
  } catch {
    return <PlainText content={content} suffix={suffix} />;
  }
  return <div className="[overflow-wrap:anywhere]">{rendered}</div>;
}
