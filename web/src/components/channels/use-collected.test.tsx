import { describe, expect, it } from "vitest";

import { buildChannelTree } from "@/components/shell/build-channel-tree";

import { toShellData } from "./use-collected";

const BASE = {
  guild_id: "1",
  position: 0,
  status: "active" as const,
  collectable: true,
  can_read: true,
};

describe("toShellData", () => {
  // covers: AC-13
  it("转成外壳的输入，线程的父频道只用来挂住线程", () => {
    const { guilds, channels } = toShellData({
      guilds: [{ id: "1", name: "Signals", icon_hash: "abc" }],
      channels: [
        { ...BASE, id: "9", parent_id: null, type: 4, name: "交易", collect_enabled: false },
        { ...BASE, id: "10", parent_id: "9", type: 0, name: "alerts", collect_enabled: false },
        { ...BASE, id: "11", parent_id: "10", type: 11, name: "t", collect_enabled: true },
      ],
    });

    expect(guilds).toEqual([{ id: "1", name: "Signals", iconHash: "abc" }]);
    const [group] = buildChannelTree(channels);
    expect(group?.name).toBe("交易");
    expect(group?.channels[0]).toMatchObject({ id: "10", selectable: false });
    expect(group?.channels[0]?.threads[0]).toMatchObject({ id: "11", selectable: true });
  });
});
