import { describe, expect, it } from "vitest";

import { buildManageTree } from "./build-manage-tree";
import type { ManagedChannel } from "./use-guild-channels";

function channel(id: string, overrides: Partial<ManagedChannel> = {}): ManagedChannel {
  return {
    id,
    guild_id: "1",
    parent_id: null,
    type: 0,
    name: id,
    position: 0,
    status: "active",
    collect_enabled: false,
    collectable: true,
    can_read: true,
    ...overrides,
  };
}

const CATEGORY = channel("cat", { type: 4, position: 1, collectable: false, can_read: false });
const OPTIONS = { query: "", showNoAccess: false };

function names(tree: ReturnType<typeof buildManageTree>) {
  return tree.groups.map((group) => [
    group.category?.name ?? null,
    group.nodes.map((node) => [node.channel.name, node.threads.map((t) => t.name)]),
  ]);
}

describe("buildManageTree", () => {
  // covers: AC-4
  it("未分类在最上，按 position 排，没有 position 的排后面再按名字", () => {
    const tree = buildManageTree(
      [
        CATEGORY,
        channel("b", { parent_id: "cat", position: null }),
        channel("a", { parent_id: "cat", position: null }),
        channel("z", { parent_id: "cat", position: 1 }),
        channel("loose", { position: 9 }),
      ],
      OPTIONS,
    );

    expect(names(tree)).toEqual([
      [null, [["loose", []]]],
      [
        "cat",
        [
          ["z", []],
          ["a", []],
          ["b", []],
        ],
      ],
    ]);
  });

  // covers: AC-4
  it("线程挂在父频道下，论坛作为分组", () => {
    const forum = channel("forum", { type: 15, collectable: false });
    const post = channel("post", { type: 11, parent_id: "forum", position: null });

    const tree = buildManageTree([forum, post], OPTIONS);

    expect(names(tree)).toEqual([[null, [["forum", ["post"]]]]]);
  });

  // covers: AC-5
  it("默认隐藏无权限频道并计数，已开启的不算隐藏", () => {
    const blocked = channel("blocked", { can_read: false });
    const lost = channel("lost", { can_read: false, collect_enabled: true });

    const hidden = buildManageTree([blocked, lost, channel("ok")], OPTIONS);
    const shown = buildManageTree([blocked, lost], { query: "", showNoAccess: true });

    expect(hidden.hiddenCount).toBe(1);
    expect(hidden.groups[0]?.nodes.map((n) => n.channel.id)).toEqual(["lost", "ok"]);
    expect(shown.hiddenCount).toBe(0);
    expect(shown.groups[0]?.nodes.map((n) => n.channel.id)).toEqual(["blocked", "lost"]);
  });

  // covers: AC-5
  it("不可采集的类型不计入隐藏数量", () => {
    const forum = channel("forum", { type: 15, collectable: false, can_read: false });

    expect(buildManageTree([forum], OPTIONS).hiddenCount).toBe(0);
  });

  // covers: AC-10
  it("搜索不区分大小写，保留匹配项的分类和父频道", () => {
    const parent = channel("parent", { parent_id: "cat" });
    const thread = channel("Alpha", { type: 11, parent_id: "parent", position: null });
    const other = channel("other", { type: 11, parent_id: "parent", position: null });

    const tree = buildManageTree([CATEGORY, parent, thread, other, channel("x")], {
      query: " ALPH ",
      showNoAccess: false,
    });

    expect(names(tree)).toEqual([["cat", [["parent", ["Alpha"]]]]]);
  });

  // covers: AC-10
  it("没有匹配时没有分组", () => {
    expect(buildManageTree([channel("a")], { query: "zzz", showNoAccess: false }).groups).toEqual(
      [],
    );
  });

  it("无权限的父频道被已开启的线程带出来", () => {
    const parent = channel("parent", { can_read: false });
    const thread = channel("t", {
      type: 11,
      parent_id: "parent",
      collect_enabled: true,
      can_read: false,
    });

    expect(names(buildManageTree([parent, thread], OPTIONS))).toEqual([
      [null, [["parent", ["t"]]]],
    ]);
  });
});
