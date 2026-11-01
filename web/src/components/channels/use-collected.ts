import { useQuery } from "@tanstack/react-query";

import type { ChannelInput } from "@/components/shell/build-channel-tree";
import type { GuildInput } from "@/components/shell/types";
import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { COLLECTED_KEY } from "@/lib/events/event-stream";

import { CHANNELS_POLL_INTERVAL_MS } from "./use-guilds";

type Collected = components["schemas"]["CollectedOut"];

export type ShellData = Readonly<{
  guilds: readonly GuildInput[];
  channels: readonly ChannelInput[];
}>;

// 接口返回的数据转成 spec 0003 外壳的输入。
export function toShellData({ guilds, channels }: Collected): ShellData {
  return {
    guilds: guilds.map((guild) => ({ id: guild.id, name: guild.name, iconHash: guild.icon_hash })),
    channels: channels.map((channel) => ({
      id: channel.id,
      guildId: channel.guild_id,
      parentId: channel.parent_id,
      type: channel.type,
      name: channel.name,
      position: channel.position,
      collectEnabled: channel.collect_enabled,
      status: channel.status,
    })),
  };
}

export function useCollected() {
  return useQuery({
    queryKey: COLLECTED_KEY,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/discord/collected");
      if (!data) throw toApiError(response.status, error);
      return toShellData(data);
    },
    refetchInterval: CHANNELS_POLL_INTERVAL_MS,
  });
}
