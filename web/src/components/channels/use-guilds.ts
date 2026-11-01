import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { GUILDS_KEY } from "@/lib/events/event-stream";

export type GuildSummary = components["schemas"]["GuildOut"];
export type GuildList = components["schemas"]["GuildsOut"];

// SSE 事件丢了或断开时，靠这个轮询更新页面（spec 0006 AC-12）。
export const CHANNELS_POLL_INTERVAL_MS = 30_000;

export function useGuilds() {
  return useQuery({
    queryKey: GUILDS_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/discord/guilds");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: CHANNELS_POLL_INTERVAL_MS,
  });
}
