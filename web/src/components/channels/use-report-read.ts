import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "@/lib/api/client";
import { UNREAD_KEY } from "@/lib/events/event-stream";
import { usePageVisible } from "@/lib/use-page-visible";

import { type UnreadCounts, withoutChannel } from "./use-unread-counts";

export const REPORT_DEBOUNCE_MS = 300;

// 频道页上最新一条消息变化、且标签页可见时，把它上报为已读位置（spec 0010 AC-6）。
// 标签页在后台时不上报；重新可见时 visible 变化，会再上报一次。失败只记日志，保持未读，
// 下一次触发（新消息、重新可见）会再试，不弹提示（AC-8）。
export function useReportRead(channelId: string, latestId: string | null, ready: boolean): void {
  const queryClient = useQueryClient();
  const visible = usePageVisible();

  useEffect(() => {
    if (!ready || !visible || latestId === null) return;
    const timer = setTimeout(async () => {
      try {
        const { response } = await api.POST("/api/discord/channels/{channel_id}/read", {
          params: { path: { channel_id: channelId } },
          body: { message_id: latestId },
        });
        if (!response.ok) throw new Error(`mark read failed: ${response.status}`);
        queryClient.setQueryData<UnreadCounts>(UNREAD_KEY, (counts) =>
          withoutChannel(counts, channelId),
        );
        void queryClient.invalidateQueries({ queryKey: UNREAD_KEY });
      } catch (error) {
        console.warn("mark read failed", error);
      }
    }, REPORT_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [channelId, latestId, ready, visible, queryClient]);
}
