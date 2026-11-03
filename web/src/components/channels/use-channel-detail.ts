import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { CHANNEL_DETAIL_KEY } from "@/lib/events/event-stream";

import { CHANNELS_POLL_INTERVAL_MS } from "./use-guilds";

export type ChannelDetail = components["schemas"]["ChannelDetailOut"];

export function useChannelDetail(channelId: string) {
  return useQuery({
    queryKey: [...CHANNEL_DETAIL_KEY, channelId],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/discord/channels/{channel_id}", {
        params: { path: { channel_id: channelId } },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    refetchInterval: CHANNELS_POLL_INTERVAL_MS,
    retry: (count, error) => count < 2 && error instanceof TypeError,
  });
}
