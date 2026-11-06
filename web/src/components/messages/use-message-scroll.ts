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
// 跳到上下文后，目标消息的背景高亮持续这么久，之后淡出（spec 0013 AC-17）。
export const HIGHLIGHT_MS = 2000;

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
  /** 第一次定位时要居中的消息 ID；没有就停在底部（spec 0013 AC-17）。 */
  centerId: string | null;
  /** 还没加载到最新消息：不贴底，滚到底部附近时向下加载（AC-19）。 */
  hasNewer: boolean;
  isLoadingNewer: boolean;
  hasNewerError: boolean;
  onReachBottom: () => void;
}>;

export type MessageScroll = Readonly<{
  /** 初次定位到底部是否已完成；完成之前不触发 `onReachTop`。 */
  ready: boolean;
  atBottom: boolean;
  newCount: number;
  onScroll: () => void;
  jumpToLatest: () => void;
  /** 正在高亮的消息 ID（跳到上下文的目标，约 2 秒后变回 null）。 */
  highlightId: string | null;
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
  const requestedNewerRef = useRef(false);
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const highlightTimer = useRef<number | null>(null);
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

  const startHighlight = useCallback((id: string) => {
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    setHighlightId(id);
    highlightTimer.current = window.setTimeout(() => {
      highlightTimer.current = null;
      setHighlightId(null);
    }, HIGHLIGHT_MS);
  }, []);

  useEffect(
    () => () => {
      if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    },
    [],
  );

  const maybeRequestNewer = useCallback(() => {
    const container = latest.current.scrollRef.current;
    const { hasNewer, isLoadingNewer, hasNewerError, onReachBottom } = latest.current;
    if (!container || requestedNewerRef.current || !hasNewer || isLoadingNewer || hasNewerError)
      return;
    if (distanceFromBottom(container) > container.clientHeight) return;
    requestedNewerRef.current = true;
    onReachBottom();
  }, []);

  // 初次定位：有中心消息就把它滚到视口正中，没有就先滚到最后一行，
  // 等下一帧行高测量完再滚一次，确保真的在底部。
  useLayoutEffect(() => {
    if (ready || lastIndex < 0) return;
    const centerKey = latest.current.centerId;
    const centerIndex =
      centerKey === null ? -1 : latest.current.rows.findIndex((row) => row.key === centerKey);
    if (centerKey !== null && centerIndex >= 0) {
      // 行高是边滚边测量的，所以每帧按 DOM 实测位置把目标行校正到正中，直到连续几帧不再动。
      restoringRef.current = true;
      let frame = 0;
      let stableFrames = 0;
      let handle = 0;
      const settle = () => {
        const container = latest.current.scrollRef.current;
        const row = container?.querySelector<HTMLElement>(`[data-key="${CSS.escape(centerKey)}"]`);
        if (container && row) {
          const box = container.getBoundingClientRect();
          const rect = row.getBoundingClientRect();
          const delta = rect.top + rect.height / 2 - (box.top + box.height / 2);
          const before = container.scrollTop;
          if (Math.abs(delta) >= 1) container.scrollTop += delta;
          // 已经到头（比如目标是最后一条）滚不动了，也算稳定。
          if (Math.abs(delta) < 1 || container.scrollTop === before) stableFrames += 1;
          else stableFrames = 0;
        } else {
          const index = latest.current.rows.findIndex((r) => r.key === centerKey);
          if (index >= 0) latest.current.virtualizer.scrollToIndex(index, { align: "center" });
          stableFrames = 0;
        }
        frame += 1;
        if (stableFrames < SETTLE_FRAMES && frame < MAX_RESTORE_FRAMES) {
          handle = requestAnimationFrame(settle);
          return;
        }
        restoringRef.current = false;
        const done = latest.current.scrollRef.current;
        markAtBottom(
          !latest.current.hasNewer && done !== null && distanceFromBottom(done) <= NEAR_BOTTOM_PX,
        );
        setReady(true);
        startHighlight(centerKey);
      };
      virtualizer.scrollToIndex(centerIndex, { align: "center" });
      handle = requestAnimationFrame(settle);
      return () => {
        cancelAnimationFrame(handle);
        restoringRef.current = false;
      };
    }
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
  }, [ready, lastIndex, scrollToEnd, markAtBottom, startHighlight, virtualizer]);

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

  // 向下加载的请求结束后允许下一次触发；加载完还停在底部附近时继续往下加载。
  // biome-ignore lint/correctness/useExhaustiveDependencies: 这些值变化时都要重新检查是否该触发
  useEffect(() => {
    if (params.isLoadingNewer) return;
    requestedNewerRef.current = false;
    if (ready) maybeRequestNewer();
  }, [
    params.isLoadingNewer,
    params.hasNewer,
    params.hasNewerError,
    rows.length,
    ready,
    maybeRequestNewer,
  ]);

  // 一路向下加载到最新之后恢复普通行为：如果这时就在底部，开始贴底（AC-19）。
  const hadNewerRef = useRef(params.hasNewer);
  useEffect(() => {
    const hadNewer = hadNewerRef.current;
    hadNewerRef.current = params.hasNewer;
    const container = scrollRef.current;
    if (
      hadNewer &&
      !params.hasNewer &&
      container &&
      distanceFromBottom(container) <= NEAR_BOTTOM_PX
    )
      markAtBottom(true);
  }, [params.hasNewer, scrollRef, markAtBottom]);

  const onScroll = useCallback(() => {
    const container = latest.current.scrollRef.current;
    if (!container) return;
    // 还没加载到最新消息时，滚到底部附近不算「在底部」：不贴底，也不当作已读（AC-19）。
    const bottom = distanceFromBottom(container) <= NEAR_BOTTOM_PX && !latest.current.hasNewer;
    if (bottom !== atBottomRef.current || bottom) markAtBottom(bottom);
    if (requestedRef.current) captureAnchor();
    else {
      liveAnchorRef.current = bottom
        ? null
        : anchorFromVirtualizer(latest.current.virtualizer, container.scrollTop);
      maybeRequestOlder();
    }
    maybeRequestNewer();
  }, [markAtBottom, maybeRequestOlder, maybeRequestNewer, captureAnchor]);

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
    highlightId,
  };
}
