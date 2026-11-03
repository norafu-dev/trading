import { describe, expect, it } from "vitest";

import type { ChannelInput } from "./build-channel-tree";
import { resolveGuildId, sortGuilds } from "./resolve-guild";
import { channelHref } from "./routes";
import type { GuildInput } from "./types";

const guilds: readonly GuildInput[] = [
  { id: "g2", name: "独立游戏开发", iconHash: null },
  { id: "g1", name: "🎧 音乐分享", iconHash: null },
];

const channels: readonly ChannelInput[] = [
  {
    id: "c-music",
    guildId: "g1",
    parentId: null,
    type: 0,
    name: "每日推荐",
    position: 0,
    collectEnabled: true,
    status: "active",
  },
  {
    id: "c-game",
    guildId: "g2",
    parentId: null,
    type: 0,
    name: "Godot 4.4 迁移",
    position: 0,
    collectEnabled: true,
    status: "active",
  },
];

describe("resolveGuildId", () => {
  // covers: AC-2
  it("刚点过的服务器优先，只要选中的频道没变", () => {
    expect(resolveGuildId({ guildId: "g1", channelId: "c-game" }, "c-game", channels, guilds)).toBe(
      "g1",
    );
  });

  it("频道一变，之前点的服务器就失效，改为跟着频道所在的服务器", () => {
    expect(
      resolveGuildId({ guildId: "g1", channelId: "c-music" }, "c-game", channels, guilds),
    ).toBe("g2");
  });

  it("没点过服务器时跟着选中频道的 guildId", () => {
    expect(resolveGuildId(null, "c-music", channels, guilds)).toBe("g1");
  });

  it("没选频道时用按名字排在第一的服务器", () => {
    expect(resolveGuildId(null, null, channels, guilds)).toBe(sortGuilds(guilds)[0]?.id);
  });

  it("选中的频道不存在时也回到第一个服务器", () => {
    expect(resolveGuildId(null, "missing", channels, guilds)).toBe(sortGuilds(guilds)[0]?.id);
  });

  it("没有任何服务器时返回 null", () => {
    expect(resolveGuildId(null, null, [], [])).toBeNull();
  });
});

describe("sortGuilds", () => {
  it("返回排好序的新数组，不修改原数组", () => {
    const sorted = sortGuilds(guilds);
    expect(sorted).not.toBe(guilds);
    expect(guilds.map((guild) => guild.id)).toEqual(["g2", "g1"]);
    expect([...sorted].sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))).toEqual(sorted);
  });
});

describe("channelHref", () => {
  it("频道页地址带编码后的 id", () => {
    expect(channelHref("123")).toBe("/channels?id=123");
    expect(channelHref("a&b")).toBe("/channels?id=a%26b");
  });
});
