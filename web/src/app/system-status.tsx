"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";

type Heartbeat = { server_time: string };

function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/health");
      if (!data) throw new Error(`health check failed (${response.status})`);
      return data;
    },
    refetchInterval: 15_000,
  });
}

function useLastHeartbeat() {
  const [lastHeartbeat, setLastHeartbeat] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.addEventListener("heartbeat", (event) => {
      const beat = JSON.parse(event.data) as Heartbeat;
      setLastHeartbeat(beat.server_time);
    });
    return () => source.close();
  }, []);

  return { connected, lastHeartbeat };
}

export function SystemStatus() {
  const health = useHealth();
  const { connected, lastHeartbeat } = useLastHeartbeat();

  return (
    <dl>
      <dt>API</dt>
      <dd>
        {health.isPending
          ? "检查中…"
          : health.isError
            ? `不可用：${health.error.message}`
            : `正常，数据库 ${health.data.database}`}
      </dd>
      <dt>数据库时间</dt>
      <dd>
        {health.data?.database_time
          ? new Date(health.data.database_time).toLocaleString()
          : "—"}
      </dd>
      <dt>实时推送（SSE）</dt>
      <dd>
        {connected ? "已连接" : "未连接"}
        {lastHeartbeat ? `，最近心跳 ${new Date(lastHeartbeat).toLocaleTimeString()}` : ""}
      </dd>
    </dl>
  );
}
