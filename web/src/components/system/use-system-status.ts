import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

export type SystemStatusData = components["schemas"]["SystemStatusOut"];
export type BackupRun = components["schemas"]["BackupRunOut"];

export const SYSTEM_STATUS_KEY = ["system-status"] as const;

// 进程心跳每 30 秒写一次，这里同样 30 秒取一次，恢复后最多 60 秒内提示消失（spec 0009 AC-4）。
const POLL_INTERVAL_MS = 30_000;

export function useSystemStatus() {
  return useQuery({
    queryKey: SYSTEM_STATUS_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/system/status");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: POLL_INTERVAL_MS,
  });
}
