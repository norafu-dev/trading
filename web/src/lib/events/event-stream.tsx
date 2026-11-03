"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useRef, useState } from "react";

import { AUTH_STATE_KEY } from "@/lib/auth/use-auth-state";

export const DISCORD_ACCOUNT_KEY = ["discord-account"] as const;
export const GUILDS_KEY = ["discord-guilds"] as const;
export const GUILD_CHANNELS_KEY = ["discord-guild-channels"] as const;
export const COLLECTED_KEY = ["discord-collected"] as const;
export const CHANNEL_DETAIL_KEY = ["discord-channel-detail"] as const;

// 消息事件只是「该取数了」的提醒，内容都从消息接口取（spec 0008）。ID 是字符串。
export type MessageStreamEvent =
  | Readonly<{ type: "new_message"; channel_id: string; message_id: string }>
  | Readonly<{ type: "message_changed"; channel_id: string; message_ids: readonly string[] }>
  | Readonly<{ type: "reconnected" }>;

type MessageListener = (event: MessageStreamEvent) => void;
type Subscribe = (listener: MessageListener) => () => void;

type Heartbeat = { server_time: string };

type EventStreamState = Readonly<{ connected: boolean; lastHeartbeat: string | null }>;

const EventStreamContext = createContext<EventStreamState>({
  connected: false,
  lastHeartbeat: null,
});

const MessageEventsContext = createContext<Subscribe>(() => () => {});

// 整个 dashboard 共用一条 SSE 连接。账号状态变化和重新连上时都让账号查询失效（spec 0005）；
// 频道数据变化和重新连上时，频道查询一起失效（spec 0006 AC-12）。
// 消息事件转发给订阅者，不写进 React 状态，这样每条消息不会让整个外壳重新渲染（spec 0008）。
export function EventStreamProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [state, setState] = useState<EventStreamState>({ connected: false, lastHeartbeat: null });
  const queryClient = useQueryClient();
  const listeners = useRef(new Set<MessageListener>());
  const [subscribe] = useState<Subscribe>(() => (listener: MessageListener) => {
    listeners.current.add(listener);
    return () => listeners.current.delete(listener);
  });

  useEffect(() => {
    const refreshAccount = () => {
      void queryClient.invalidateQueries({ queryKey: DISCORD_ACCOUNT_KEY });
      void queryClient.invalidateQueries({ queryKey: CHANNEL_DETAIL_KEY });
    };
    const refreshChannels = () => {
      for (const queryKey of [GUILDS_KEY, GUILD_CHANNELS_KEY, COLLECTED_KEY, CHANNEL_DETAIL_KEY]) {
        void queryClient.invalidateQueries({ queryKey });
      }
    };
    const emit = (event: MessageStreamEvent) => {
      for (const listener of listeners.current) listener(event);
    };
    let opened = false;
    const source = new EventSource("/api/events");
    source.onopen = () => {
      setState((current) => ({ ...current, connected: true }));
      refreshAccount();
      refreshChannels();
      // 第一次连上时页面正在取数，只有之后的重连才可能漏了消息。
      if (opened) emit({ type: "reconnected" });
      opened = true;
    };
    source.onerror = () => {
      setState((current) => ({ ...current, connected: false }));
      // 可能是会话失效被断开；重新查登录状态，由 AuthGate 决定要不要跳转（spec 0004 AC-14）。
      void queryClient.invalidateQueries({ queryKey: AUTH_STATE_KEY });
    };
    source.addEventListener("heartbeat", (event) => {
      const beat = JSON.parse(event.data) as Heartbeat;
      setState((current) => ({ ...current, lastHeartbeat: beat.server_time }));
    });
    source.addEventListener("account_status", refreshAccount);
    source.addEventListener("channels_changed", refreshChannels);
    for (const name of ["new_message", "message_changed"]) {
      source.addEventListener(name, (event) => emit(JSON.parse(event.data) as MessageStreamEvent));
    }
    return () => source.close();
  }, [queryClient]);

  return (
    <EventStreamContext.Provider value={state}>
      <MessageEventsContext.Provider value={subscribe}>{children}</MessageEventsContext.Provider>
    </EventStreamContext.Provider>
  );
}

// 订阅消息事件。listener 变化不会重新订阅，总是调用最新的那个。
export function useMessageEvents(listener: MessageListener): void {
  const subscribe = useContext(MessageEventsContext);
  const latest = useRef(listener);
  latest.current = listener;
  useEffect(() => subscribe((event) => latest.current(event)), [subscribe]);
}

export function useEventStream(): EventStreamState {
  return useContext(EventStreamContext);
}
