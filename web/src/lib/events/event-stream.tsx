"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { AUTH_STATE_KEY } from "@/lib/auth/use-auth-state";

export const DISCORD_ACCOUNT_KEY = ["discord-account"] as const;

type Heartbeat = { server_time: string };

type EventStreamState = Readonly<{ connected: boolean; lastHeartbeat: string | null }>;

const EventStreamContext = createContext<EventStreamState>({
  connected: false,
  lastHeartbeat: null,
});

// 整个 dashboard 共用一条 SSE 连接。账号状态变化和重新连上时都让账号查询失效（spec 0005）。
export function EventStreamProvider({ children }: Readonly<{ children: ReactNode }>) {
  const [state, setState] = useState<EventStreamState>({ connected: false, lastHeartbeat: null });
  const queryClient = useQueryClient();

  useEffect(() => {
    const refreshAccount = () =>
      void queryClient.invalidateQueries({ queryKey: DISCORD_ACCOUNT_KEY });
    const source = new EventSource("/api/events");
    source.onopen = () => {
      setState((current) => ({ ...current, connected: true }));
      refreshAccount();
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
    return () => source.close();
  }, [queryClient]);

  return <EventStreamContext.Provider value={state}>{children}</EventStreamContext.Provider>;
}

export function useEventStream(): EventStreamState {
  return useContext(EventStreamContext);
}
