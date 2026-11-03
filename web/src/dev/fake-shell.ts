// 预览页用的假服务器和频道（只被 /dev/preview 引入）。标记：__HARVESTER_FAKE_DATA__
import type { ChannelInput } from "@/components/shell/build-channel-tree";
import type { GuildInput } from "@/components/shell/types";

export const FAKE_GUILDS: readonly GuildInput[] = [
  { id: "900000000000000001", name: "独立游戏开发", iconHash: null },
  { id: "900000000000000002", name: "🎧 音乐分享", iconHash: null },
  { id: "900000000000000003", name: "Rust 中文社区", iconHash: null },
];

const G1 = "900000000000000001";
const G2 = "900000000000000002";
const G3 = "900000000000000003";

function channel(
  id: string,
  guildId: string,
  name: string,
  type: number,
  parentId: string | null,
  position: number | null,
  collectEnabled = true,
  status: ChannelInput["status"] = "active",
): ChannelInput {
  return { id, guildId, parentId, type, name, position, collectEnabled, status };
}

export const FAKE_CHANNELS: readonly ChannelInput[] = [
  channel("910000000000000001", G1, "公告", 4, null, 0, false),
  channel("910000000000000002", G1, "讨论", 4, null, 1, false),
  channel("910000000000000003", G1, "欢迎", 0, null, 0),
  channel("910000000000000004", G1, "更新日志", 5, "910000000000000001", 0),
  channel("910000000000000005", G1, "活动预告", 0, "910000000000000001", 1),
  channel("910000000000000006", G1, "闲聊", 0, "910000000000000002", 0),
  channel("910000000000000007", G1, "美术", 0, "910000000000000002", 1, false),
  channel("910000000000000008", G1, "像素风格求助", 11, "910000000000000007", null),
  channel("910000000000000009", G1, "程序", 0, "910000000000000002", 2),
  channel("910000000000000010", G1, "Godot 4.4 迁移", 11, "910000000000000009", null),
  channel(
    "910000000000000011",
    G1,
    "存档格式讨论",
    11,
    "910000000000000009",
    null,
    true,
    "archived",
  ),
  channel("910000000000000012", G1, "已删掉的频道", 0, "910000000000000002", 3, true, "deleted"),
  channel("910000000000000013", G1, "反馈论坛", 15, "910000000000000002", 4, false),
  channel("910000000000000014", G1, "存档丢失了怎么办", 11, "910000000000000013", null),
  channel("910000000000000015", G1, "没有排序位置的频道", 0, "910000000000000002", null),
  channel("920000000000000001", G2, "每日推荐", 0, null, 0),
  channel("920000000000000002", G2, "黑胶交流", 0, null, 1),
  channel("930000000000000001", G3, "新手提问", 0, null, 0, false),
];

export const FAKE_SHELL_MARKER = "__HARVESTER_FAKE_DATA__";
