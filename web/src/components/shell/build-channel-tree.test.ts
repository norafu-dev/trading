import { describe, expect, it } from "vitest";

import { buildChannelTree, type ChannelInput } from "./build-channel-tree";

const TEXT = 0;
const CATEGORY = 4;
const PUBLIC_THREAD = 11;

function channel(id: string, overrides: Partial<ChannelInput> = {}): ChannelInput {
  return {
    id,
    guildId: "g1",
    parentId: null,
    type: TEXT,
    name: `频道 ${id}`,
    position: 0,
    collectEnabled: true,
    status: "active",
    ...overrides,
  };
}

const category = (id: string, name: string, position: number | null) =>
  channel(id, { type: CATEGORY, name, position, collectEnabled: false });

describe("buildChannelTree", () => {
  // covers: AC-2
  it("分类按 position 排序，不属于分类的频道排在最前面", () => {
    const groups = buildChannelTree([
      category("c2", "讨论", 2),
      category("c1", "公告", 1),
      channel("a", { parentId: "c2" }),
      channel("b", { parentId: "c1" }),
      channel("loose"),
    ]);
    expect(groups.map((group) => group.name)).toEqual([null, "公告", "讨论"]);
  });

  it("分类里的频道按 position 排序，没有 position 的排最后，再按名字", () => {
    const groups = buildChannelTree([
      category("c1", "讨论", 1),
      channel("x", { parentId: "c1", name: "乙", position: null }),
      channel("y", { parentId: "c1", name: "甲", position: null }),
      channel("z", { parentId: "c1", position: 5 }),
      channel("w", { parentId: "c1", position: 1 }),
    ]);
    expect(groups[0]?.channels.map((node) => node.id)).toEqual(["w", "z", "y", "x"]);
  });

  it("只列出开了采集且没被删除的频道", () => {
    const groups = buildChannelTree([
      channel("on"),
      channel("off", { collectEnabled: false }),
      channel("gone", { status: "deleted" }),
      channel("old", { status: "archived", position: 1 }),
    ]);
    expect(groups[0]?.channels.map((node) => node.id)).toEqual(["on", "old"]);
  });

  it("线程挂在父频道下面，按 position 排序", () => {
    const groups = buildChannelTree([
      channel("parent"),
      channel("t2", { parentId: "parent", type: PUBLIC_THREAD, position: 2 }),
      channel("t1", { parentId: "parent", type: PUBLIC_THREAD, position: 1 }),
    ]);
    const parent = groups[0]?.channels[0];
    expect(parent?.id).toBe("parent");
    expect(parent?.selectable).toBe(true);
    expect(parent?.threads.map((node) => node.id)).toEqual(["t1", "t2"]);
    expect(groups[0]?.channels).toHaveLength(1);
  });

  it("父频道没开采集时以不可点的占位出现，只为挂住线程", () => {
    const groups = buildChannelTree([
      category("c1", "讨论", 1),
      channel("art", { parentId: "c1", name: "美术", collectEnabled: false }),
      channel("help", { parentId: "art", type: PUBLIC_THREAD, name: "像素风格求助" }),
    ]);
    const [group] = groups;
    expect(group?.name).toBe("讨论");
    expect(group?.channels).toEqual([
      {
        id: "art",
        name: "美术",
        type: TEXT,
        selectable: false,
        threads: [
          { id: "help", name: "像素风格求助", type: PUBLIC_THREAD, selectable: true, threads: [] },
        ],
      },
    ]);
  });

  it("被删除的线程不出现", () => {
    const groups = buildChannelTree([
      channel("parent"),
      channel("t", { parentId: "parent", type: PUBLIC_THREAD, status: "deleted" }),
    ]);
    expect(groups[0]?.channels[0]?.threads).toEqual([]);
  });

  it("父频道不在数据里的线程作为顶层频道出现", () => {
    const groups = buildChannelTree([
      channel("orphan", { parentId: "missing", type: PUBLIC_THREAD }),
    ]);
    expect(groups).toEqual([
      {
        categoryId: null,
        name: null,
        channels: [
          { id: "orphan", name: "频道 orphan", type: PUBLIC_THREAD, selectable: true, threads: [] },
        ],
      },
    ]);
  });

  it("没有可见频道的分类不出现；没有频道时返回空数组", () => {
    expect(buildChannelTree([category("c1", "空分类", 1)])).toEqual([]);
    expect(buildChannelTree([])).toEqual([]);
  });

  it("不修改传入的数组", () => {
    const input = Object.freeze([channel("b", { position: 2 }), channel("a", { position: 1 })]);
    expect(() => buildChannelTree(input)).not.toThrow();
    expect(input.map((item) => item.id)).toEqual(["b", "a"]);
  });
});
