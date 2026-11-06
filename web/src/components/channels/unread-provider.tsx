"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { UNREAD_KEY, useMessageEvents } from "@/lib/events/event-stream";
import { usePageVisible } from "@/lib/use-page-visible";

import { NO_UNREAD, type UnreadCounts, useUnreadQuery } from "./use-unread-counts";

export const NEW_MESSAGE_COALESCE_MS = 1000;

const UnreadContext = createContext<UnreadCounts>(NO_UNREAD);
const CurrentChannelContext = createContext<(channelId: string | null) => void>(() => {});

// 当前频道且标签页可见时，它的条数按 0 显示，避免新消息刚到时闪一下。
export function hideCurrent(
  counts: UnreadCounts,
  current: string | null,
  visible: boolean,
): UnreadCounts {
  if (current === null || !visible || !counts.has(current)) return counts;
  return new Map([...counts].filter(([id]) => id !== current));
}

// 未读数来自数据库（GET /api/discord/unread）。新消息事件在 1 秒内合并成一次重新取数，
// 重连时马上取。这里不读 useSearchParams（否则整个外壳要包进 Suspense），由频道页告诉它当前频道。
export function UnreadProvider({ children }: Readonly<{ children: ReactNode }>) {
  const queryClient = useQueryClient();
  const counts = useUnreadQuery();
  const visible = usePageVisible();
  const [current, setCurrent] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(
    () => void queryClient.invalidateQueries({ queryKey: UNREAD_KEY }),
    [queryClient],
  );

  useMessageEvents((event) => {
    if (event.type === "reconnected") return refresh();
    if (event.type !== "new_message" || timer.current !== null) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      refresh();
    }, NEW_MESSAGE_COALESCE_MS);
  });

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  const shown = useMemo(() => hideCurrent(counts, current, visible), [counts, current, visible]);

  return (
    <CurrentChannelContext.Provider value={setCurrent}>
      <UnreadContext.Provider value={shown}>{children}</UnreadContext.Provider>
    </CurrentChannelContext.Provider>
  );
}

export function useUnreadCounts(): UnreadCounts {
  return useContext(UnreadContext);
}

export function useSetCurrentChannel(): (channelId: string | null) => void {
  return useContext(CurrentChannelContext);
}
