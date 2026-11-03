// 外壳的输入类型；第 7 项把接口返回的服务器和频道转换成它们。
export type GuildInput = Readonly<{
  id: string;
  name: string;
  iconHash: string | null;
}>;

export type CollectorStatus = "online" | "offline" | "unknown";

// 你点了某个服务器图标时记下当时所在的频道；频道一变，这个选择就失效，回到跟随频道。
export type GuildChoice = Readonly<{ guildId: string; channelId: string | null }> | null;
