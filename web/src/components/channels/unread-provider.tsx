"use client";

import { createContext, type ReactNode, useCallback, useContext, useRef, useState } from "react";

import { useMessageEvents } from "@/lib/events/event-stream";

const NONE: ReadonlySet<string> = new Set();

export function markUnread(
  unread: ReadonlySet<string>,
  channelId: string,
  currentChannelId: string | null,
): ReadonlySet<string> {
  if (channelId === currentChannelId || unread.has(channelId)) return unread;
  return new Set([...unread, channelId]);
}

export function clearUnread(unread: ReadonlySet<string>, channelId: string): ReadonlySet<string> {
  if (!unread.has(channelId)) return unread;
  return new Set([...unread].filter((id) => id !== channelId));
}

const UnreadContext = createContext<ReadonlySet<string>>(NONE);
const CurrentChannelContext = createContext<(channelId: string | null) => void>(() => {});

// 未读状态只在页面内存里，刷新后清空（AC-22）。这里不读 useSearchParams（否则整个外壳要包进
// Suspense），由频道页告诉它当前频道；标签页在后台时，当前打开的频道也算在看。
export function UnreadProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [unread, setUnread] = useState(NONE);
  const current = useRef<string | null>(null);

  useMessageEvents((event) => {
    if (event.type !== "new_message") return;
    setUnread((state) => markUnread(state, event.channel_id, current.current));
  });

  const setCurrentChannel = useCallback((channelId: string | null) => {
    current.current = channelId;
    if (channelId !== null) setUnread((state) => clearUnread(state, channelId));
  }, []);

  return (
    <CurrentChannelContext.Provider value={setCurrentChannel}>
      <UnreadContext.Provider value={unread}>{children}</UnreadContext.Provider>
    </CurrentChannelContext.Provider>
  );
}

export function useUnreadChannels(): ReadonlySet<string> {
  return useContext(UnreadContext);
}

export function useSetCurrentChannel(): (channelId: string | null) => void {
  return useContext(CurrentChannelContext);
}
