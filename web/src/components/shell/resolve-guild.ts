import type { ChannelInput } from "./build-channel-tree";
import type { GuildChoice, GuildInput } from "./types";

export function sortGuilds(guilds: readonly GuildInput[]): readonly GuildInput[] {
  return [...guilds].sort(
    (a, b) => a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id),
  );
}

// 当前显示哪个服务器：你刚点过的优先；否则跟着选中频道的 guild_id；都没有就用按名字排的第一个。
export function resolveGuildId(
  choice: GuildChoice,
  selectedChannelId: string | null,
  channels: readonly ChannelInput[],
  guilds: readonly GuildInput[],
): string | null {
  if (choice !== null && choice.channelId === selectedChannelId) return choice.guildId;
  const channel = channels.find((item) => item.id === selectedChannelId);
  if (channel) return channel.guildId;
  return sortGuilds(guilds)[0]?.id ?? null;
}
