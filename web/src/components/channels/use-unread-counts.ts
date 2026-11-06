import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import { UNREAD_KEY } from "@/lib/events/event-stream";

export const UNREAD_POLL_INTERVAL_MS = 30_000;

export type UnreadCounts = ReadonlyMap<string, number>;

export const NO_UNREAD: UnreadCounts = new Map();

// 未读只来自这个接口（spec 0010）。事件、重连、窗口焦点和轮询都只是让它重新取。
export function useUnreadQuery(): UnreadCounts {
  const { data } = useQuery({
    queryKey: UNREAD_KEY,
    queryFn: async (): Promise<UnreadCounts> => {
      const { data, error, response } = await api.GET("/api/discord/unread");
      if (!data) throw toApiError(response.status, error);
      return new Map(data.channels.map((entry) => [entry.channel_id, entry.count]));
    },
    refetchInterval: UNREAD_POLL_INTERVAL_MS,
    refetchOnWindowFocus: true,
  });
  return data ?? NO_UNREAD;
}

// 上报已读成功后立刻把这个频道从缓存里去掉，不用等下一次取数。
export function withoutChannel(counts: UnreadCounts | undefined, channelId: string): UnreadCounts {
  if (counts === undefined || !counts.has(channelId)) return counts ?? NO_UNREAD;
  return new Map([...counts].filter(([id]) => id !== channelId));
}
