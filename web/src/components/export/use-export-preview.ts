import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

import type { RangeParams } from "./export-selection";

export type ExportPreview = components["schemas"]["ExportPreviewOut"];

// 范围一变，查询键就变，旧请求的结果自然被丢掉（AC-6）。
// 预览是一次性的统计，不自动重试也不缓存：库里的数字随时在变。
export function useExportPreview(channelId: string, range: RangeParams | null) {
  return useQuery({
    queryKey: ["export-preview", channelId, range?.start ?? null, range?.end ?? null],
    enabled: range !== null,
    gcTime: 0,
    staleTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const { data, error, response } = await api.GET("/api/channels/{channel_id}/export/preview", {
        params: {
          path: { channel_id: channelId },
          query: { start: range?.start, end: range?.end },
        },
        signal,
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
  });
}
