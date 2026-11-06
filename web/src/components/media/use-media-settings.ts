import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { SYSTEM_STATUS_KEY } from "@/components/system/use-system-status";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";

import type { MediaSettings } from "./describe-media";

export const MEDIA_SETTINGS_KEY = ["media-settings"] as const;

// 下载在后台一直在进行，已用容量和各状态数量隔一会儿取一次。
const POLL_INTERVAL_MS = 15_000;

export function useMediaSettings() {
  return useQuery({
    queryKey: MEDIA_SETTINGS_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/media/settings");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: POLL_INTERVAL_MS,
  });
}

function useRefreshAfter() {
  const queryClient = useQueryClient();
  return () => {
    for (const queryKey of [MEDIA_SETTINGS_KEY, SYSTEM_STATUS_KEY]) {
      void queryClient.invalidateQueries({ queryKey });
    }
  };
}

export function useSaveMediaLimits() {
  const queryClient = useQueryClient();
  const refresh = useRefreshAfter();
  return useMutation({
    mutationFn: async (body: Readonly<{ max_file_mb: number; quota_gb: number }>) => {
      const { data, error, response } = await api.PUT("/api/media/settings", { body });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: (data: MediaSettings) => {
      queryClient.setQueryData(MEDIA_SETTINGS_KEY, data);
      toast.success("已保存，几秒内生效");
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: refresh,
  });
}

export function useRetryAllFailed() {
  const refresh = useRefreshAfter();
  return useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/media/retry-failed");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: ({ requeued }) => {
      toast.success(requeued > 0 ? `已把 ${requeued} 个失败的附件排回队列` : "没有失败的附件");
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
    onSettled: refresh,
  });
}
