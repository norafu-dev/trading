import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { GUILD_CHANNELS_KEY } from "@/lib/events/event-stream";

import { CHANNELS_POLL_INTERVAL_MS } from "./use-guilds";

export type ManagedChannel = components["schemas"]["ChannelOut"];

export function guildChannelsKey(guildId: string | null) {
  return [...GUILD_CHANNELS_KEY, guildId] as const;
}

export function useGuildChannels(guildId: string | null) {
  return useQuery({
    queryKey: guildChannelsKey(guildId),
    enabled: guildId !== null,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/discord/guilds/{guild_id}/channels", {
        params: { path: { guild_id: guildId ?? "" } },
      });
      if (!data) throw toApiError(response.status, error);
      return data.channels;
    },
    refetchInterval: CHANNELS_POLL_INTERVAL_MS,
  });
}
