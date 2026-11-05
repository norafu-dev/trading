"use client";

import type { Virtualizer } from "@tanstack/react-virtual";
import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { countNewMessages, maxMessageId, type Row } from "./build-rows";
import type { MessageView } from "./types";

// 离底部不超过这个距离就算「在底部」，新消息来了自动滚到底（AC-7）。
const NEAR_BOTTOM_PX = 80;
// 插入旧消息后，锚点连续这么多帧不动才算稳定；最多校正这么多帧（约半秒）。
const SETTLE_FRAMES = 3;
const MAX_RESTORE_FRAMES = 30;

type ScrollAnchor = Readonly<{ key: string; offset: number }>;

type Params = Readonly<{
  messages: readonly MessageView[];
  rows: readonly Row[];
  virtualizer: Virtualizer<HTMLDivElement, Element>;
  scrollRef: RefObject<HTMLDivElement | null>;
  totalSize: number;
  hasOlder: boolean;
  isLoadingOlder: boolean;
  hasOlderError: boolean;
  onReachTop: () => void;
}>;

export type MessageScroll = Readonly<{
  /** 初次定位到底部是否已完成；完成之前不触发 `onReachTop`。 */
  ready: boolean;
  atBottom: boolean;
  newCount: number;
  onScroll: () => void;
  jumpToLatest: () => void;
}>;

function distanceFromBottom(element: HTMLElement): number {
  return element.scrollHeight - element.scrollTop - element.clientHeight;
}

// 某一行的顶部离滚动容器顶部的实际距离（用 DOM 量，不用估算值）。
function rowOffset(container: HTMLElement, key: string): number | null {
  const row = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (!row) return null;
  return row.getBoundingClientRect().top - container.getBoundingClientRect().top;
}

// 当前视口顶部是哪一行、它离视口顶部多远。滚动事件触发的那一刻 DOM 可能还停在上一个位置，
// 所以先用 virtualizer 自己的坐标估一个，下一帧行渲染并测量好之后再用 DOM 实测值校准。
function anchorFromVirtualizer(
  virtualizer: Virtualizer<HTMLDivElement, Element>,
  scrollTop: number,
): ScrollAnchor | null {
  const item = virtualizer.getVirtualItemForOffset(scrollTop);
  return item ? { key: String(item.key), offset: item.start - scrollTop } : null;
}

function anchorFromDom(container: HTMLElement): ScrollAnchor | null {
  const top = container.getBoundingClientRect().top;
  for (const row of container.querySelectorAll<HTMLElement>("[data-key]")) {
    const rect = row.getBoundingClientRect();
    if (rect.bottom > top && row.dataset.key)
      return { key: row.dataset.key, offset: rect.top - top };
  }
  return null;
}

// 消息列表的滚动行为（spec 0003「虚拟列表的参数」）：初次定位、贴底、新消息计数、
// 顶部触发加载更早的消息，以及插入旧消息后按 key 把你正在看的那条放回原位。
export function useMessageScroll(params: Params): MessageScroll {
  const { messages, rows, virtualizer, scrollRef, totalSize } = params;
  const [ready, setReady] = useState(false);
  const [atBottom, setAtBottom] = useState(true);
  const [lastSeenMaxId, setLastSeenMaxId] = useState<string | null>(null);
  const atBottomRef = useRef(true);
  const anchorRef = useRef<ScrollAnchor | null>(null);
  // 平时滚动就一直记着视口顶部那一行：补采把旧消息插进已加载的范围时（spec 0012 AC-17），
  // 没有「加载更早」那样的显式触发点，就用这个锚点把你正在看的那条放回原位。
  const liveAnchorRef = useRef<ScrollAnchor | null>(null);
  const requestedRef = useRef(false);
  const restoringRef = useRef(false);
  // 事件回调里要读最新的 props，又不想因此反复重建回调。
  const latest = useRef(params);
  latest.current = params;

  // 平时沿用 virtualizer 的默认判断（视口上方的行高度变了就补偿滚动位置）；插入旧消息后的恢复期间
  // 关掉它，只用下面按 DOM 实测的校正，否则两边会互相抵消、来回抖动。
  virtualizer.shouldAdjustScrollPositionOnItemSizeChange = (item, _delta, instance) =>
    !restoringRef.current && item.start < (instance.scrollOffset ?? 0);

  const firstKey = rows[0]?.key ?? null;
  const lastIndex = rows.length - 1;
  const maxId = maxMessageId(messages);

  // 先按 spec 滚到最后一行，再把底部留白也滚过去，真正停在最底。
  const scrollToEnd = useCallback(() => {
    const { rows: current, virtualizer: list, scrollRef: ref } = latest.current;
    if (current.length === 0) return;
    list.scrollToIndex(current.length - 1, { align: "end" });
    const container = ref.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, []);

  const markAtBottom = useCallback((bottom: boolean) => {
    atBottomRef.current = bottom;
    setAtBottom(bottom);
    if (bottom) setLastSeenMaxId(maxMessageId(latest.current.messages));
  }, []);

  // 加载更早消息的请求发出后、数据回来前，锚点跟着你的滚动更新。
  const captureAnchor = useCallback(() => {
    const container = latest.current.scrollRef.current;
    if (!container) return;
    anchorRef.current = anchorFromVirtualizer(latest.current.virtualizer, container.scrollTop);
    requestAnimationFrame(() => {
      if (requestedRef.current) anchorRef.current = anchorFromDom(container) ?? anchorRef.current;
    });
  }, []);

  const maybeRequestOlder = useCallback(() => {
    const container = latest.current.scrollRef.current;
    const { hasOlder, isLoadingOlder, hasOlderError, onReachTop } = latest.current;
    if (!container || requestedRef.current || !hasOlder || isLoadingOlder || hasOlderError) return;
    if (container.scrollTop > container.clientHeight) return;
    requestedRef.current = true;
    captureAnchor();
    onReachTop();
  }, [captureAnchor]);

  // 初次定位：先滚到最后一行，等下一帧行高测量完再滚一次，确保真的在底部。
  useLayoutEffect(() => {
    if (ready || lastIndex < 0) return;
    scrollToEnd();
    let second = 0;
    const first = requestAnimationFrame(() => {
      scrollToEnd();
      second = requestAnimationFrame(() => {
        markAtBottom(true);
        setReady(true);
      });
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, [ready, lastIndex, scrollToEnd, markAtBottom]);

  // 贴底时，末尾来了新消息或任一行高变化，都滚到底。
  // biome-ignore lint/correctness/useExhaustiveDependencies: maxId 和 totalSize 变化就是触发条件
  useLayoutEffect(() => {
    if (!ready || !atBottomRef.current) return;
    scrollToEnd();
    setLastSeenMaxId(maxMessageId(latest.current.messages));
  }, [ready, maxId, totalSize, scrollToEnd]);

  // 前面插入了旧消息：找到原来第一条可见行的新下标，先按估算位置跳过去；之后新行陆续被测量，
  // 每帧按 DOM 实测位置校正，直到它连续几帧都停在原来的高度。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 行数或第一行变化时恢复
  useLayoutEffect(() => {
    const anchor = anchorRef.current ?? (atBottomRef.current ? null : liveAnchorRef.current);
    const container = scrollRef.current;
    if (!anchor || !container) return;
    const index = latest.current.rows.findIndex((row) => row.key === anchor.key);
    if (index <= 0) return;
    requestedRef.current = false;
    restoringRef.current = true;
    const start = virtualizer.measurementsCache[index]?.start;
    if (start !== undefined) virtualizer.scrollToOffset(start - anchor.offset);

    let frame = 0;
    let stableFrames = 0;
    let handle = 0;
    const correct = () => {
      const offset = rowOffset(container, anchor.key);
      const delta = offset === null ? 0 : offset - anchor.offset;
      if (Math.abs(delta) >= 1) {
        container.scrollTop += delta;
        stableFrames = 0;
      } else {
        stableFrames += 1;
      }
      frame += 1;
      if (stableFrames < SETTLE_FRAMES && frame < MAX_RESTORE_FRAMES) {
        handle = requestAnimationFrame(correct);
      } else {
        anchorRef.current = null;
        restoringRef.current = false;
      }
    };
    handle = requestAnimationFrame(correct);
    return () => {
      cancelAnimationFrame(handle);
      restoringRef.current = false;
    };
  }, [firstKey, rows.length]);

  // 加载结束（成功插入或失败）后允许下一次触发；内容还不够一屏时继续往前加载。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 这些值变化时都要重新检查是否该触发
  useEffect(() => {
    if (params.isLoadingOlder) return;
    requestedRef.current = false;
    if (ready) maybeRequestOlder();
  }, [
    params.isLoadingOlder,
    params.hasOlder,
    params.hasOlderError,
    firstKey,
    ready,
    maybeRequestOlder,
  ]);

  const onScroll = useCallback(() => {
    const container = latest.current.scrollRef.current;
    if (!container) return;
    const bottom = distanceFromBottom(container) <= NEAR_BOTTOM_PX;
    if (bottom !== atBottomRef.current || bottom) markAtBottom(bottom);
    if (requestedRef.current) captureAnchor();
    else {
      liveAnchorRef.current = bottom
        ? null
        : anchorFromVirtualizer(latest.current.virtualizer, container.scrollTop);
      maybeRequestOlder();
    }
  }, [markAtBottom, maybeRequestOlder, captureAnchor]);

  const jumpToLatest = useCallback(() => {
    markAtBottom(true);
    scrollToEnd();
    requestAnimationFrame(scrollToEnd);
  }, [markAtBottom, scrollToEnd]);

  return {
    ready,
    atBottom,
    newCount: atBottom ? 0 : countNewMessages(messages, lastSeenMaxId),
    onScroll,
    jumpToLatest,
  };
}
