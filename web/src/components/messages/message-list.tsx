"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { MessageSquareDashedIcon } from "lucide-react";
import { useMemo, useRef } from "react";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { ListSkeleton } from "@/components/states/list-skeleton";
import { type ParserStatus, useMarkdownParser } from "@/lib/discord-markdown/use-markdown-parser";
import { createTimeFormat } from "@/lib/format/time";

import { buildRows, type Row } from "./build-rows";
import { DayDivider } from "./day-divider";
import { JumpToLatestButton } from "./jump-to-latest-button";
import { MessageRow } from "./message-row";
import { NewerLoader } from "./newer-loader";
import { OlderLoader } from "./older-loader";
import type { MessageListProps } from "./types";
import { useMessageScroll } from "./use-message-scroll";

// 行高估算值，真实高度由 measureElement 实测（spec 0003）。
const GROUP_START_ESTIMATE = 72;
const FOLLOW_UP_ESTIMATE = 28;
const DAY_ESTIMATE = 40;

function noop() {}

function estimateFor(row: Row | undefined): number {
  if (!row || row.kind === "day") return DAY_ESTIMATE;
  return row.groupStart ? GROUP_START_ESTIMATE : FOLLOW_UP_ESTIMATE;
}

// 虚拟滚动的消息列表：只渲染屏幕上看得见的几十行。切换频道时请用 key 重新挂载它。
export function MessageList(props: MessageListProps) {
  const { messages, status } = props;
  const parser = useMarkdownParser();
  if (status === "loading") return <ListSkeleton />;
  if (status === "error" && props.error) {
    return <ErrorBlock error={props.error} onRetry={props.onRetry} className="h-full" />;
  }
  if (status === "empty" || messages.length === 0) {
    return (
      <EmptyState
        icon={MessageSquareDashedIcon}
        title="这个频道还没有采集到消息"
        description="新消息到来后会出现在这里。"
        className="h-full"
      />
    );
  }
  // 解析器就绪之前显示骨架，不先显示纯文本再切换（避免行高跳动）。
  if (parser === "pending") return <ListSkeleton />;
  return <VirtualMessageList {...props} parser={parser} />;
}

function VirtualMessageList({
  messages,
  resolver,
  now,
  timeZone,
  hasOlder,
  isLoadingOlder,
  olderError,
  onReachTop,
  onRetry,
  centerId = null,
  hasNewer = false,
  isLoadingNewer = false,
  newerError,
  onReachBottom,
  onRetryNewer,
  onJumpLatest,
  parser,
}: MessageListProps & Readonly<{ parser: ParserStatus }>) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const rows = useMemo(() => buildRows(messages, timeZone), [messages, timeZone]);
  const format = useMemo(() => createTimeFormat(timeZone), [timeZone]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => estimateFor(rows[index]),
    getItemKey: (index) => rows[index]?.key ?? index,
    overscan: 10,
    paddingEnd: 16,
  });
  const items = virtualizer.getVirtualItems();
  const totalSize = virtualizer.getTotalSize();

  const scroll = useMessageScroll({
    messages,
    rows,
    virtualizer,
    scrollRef,
    totalSize,
    hasOlder,
    isLoadingOlder,
    hasOlderError: olderError !== undefined,
    onReachTop,
    centerId,
    hasNewer,
    isLoadingNewer,
    hasNewerError: newerError !== undefined,
    onReachBottom: onReachBottom ?? noop,
  });

  return (
    <div className="relative h-full">
      <OlderLoader isLoading={isLoadingOlder} error={olderError} onRetry={onRetry} />
      <NewerLoader isLoading={isLoadingNewer} error={newerError} onRetry={onRetryNewer} />
      <div
        ref={scrollRef}
        onScroll={scroll.onScroll}
        role="log"
        aria-label="消息"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: 滚动区域要能用键盘聚焦后上下滚动
        tabIndex={0}
        className="h-full overflow-y-auto overscroll-contain [overflow-anchor:none]"
      >
        <div className="relative w-full" style={{ height: totalSize }}>
          <div
            className="absolute top-0 left-0 w-full"
            style={{ transform: `translateY(${items[0]?.start ?? 0}px)` }}
          >
            {items.map((item) => {
              const row = rows[item.index];
              if (!row) return null;
              return row.kind === "day" ? (
                <div
                  key={row.key}
                  data-key={row.key}
                  data-index={item.index}
                  ref={virtualizer.measureElement}
                >
                  <DayDivider dayKey={row.dayKey} />
                </div>
              ) : (
                // 组与组之间的 16px 用测量容器的内边距，不用外边距：外边距会折叠到容器外面，
                // 测出来的行高就少了 16px，滚动位置会跟着跳。
                <div
                  key={row.key}
                  data-key={row.key}
                  data-index={item.index}
                  data-message-row
                  ref={virtualizer.measureElement}
                  className={row.groupStart ? "pt-4" : undefined}
                >
                  <MessageRow
                    row={row}
                    format={format}
                    now={now}
                    resolver={resolver}
                    parser={parser}
                    highlighted={row.key === scroll.highlightId}
                  />
                </div>
              );
            })}
          </div>
        </div>
      </div>
      {scroll.ready && (hasNewer || !scroll.atBottom) ? (
        <JumpToLatestButton
          newCount={scroll.newCount}
          onClick={hasNewer && onJumpLatest ? onJumpLatest : scroll.jumpToLatest}
        />
      ) : null}
    </div>
  );
}
