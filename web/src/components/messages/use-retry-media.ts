import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";

// 手动重试一个保存失败或已失效的附件（spec 0011 AC-17）。成功后后端会发 message_changed，
// 打开着的频道页自己刷新这条消息，所以这里不用动消息缓存。
export function useRetryMedia() {
  return useMutation({
    mutationFn: async (mediaId: string) => {
      const { data, error, response } = await api.POST("/api/media/{media_id}/retry", {
        params: { path: { media_id: Number(mediaId) } },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onError: (error) => {
      const { title, detail } = toUiError(error);
      toast.error(title, { description: detail });
    },
  });
}
