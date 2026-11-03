// 把一个服务器的频道整理成管理页的树（spec 0006，AC-4、AC-5、AC-10）。纯函数。

import { compareChannels } from "@/components/shell/build-channel-tree";

import type { ManagedChannel } from "./use-guild-channels";

const CATEGORY = 4;
const THREAD_TYPES: ReadonlySet<number> = new Set([10, 11, 12]);

export type ManageNode = Readonly<{
  channel: ManagedChannel;
  threads: readonly ManagedChannel[];
}>;

export type ManageGroup = Readonly<{
  /** 不属于任何分类的频道为 null，这一组排在最前面。 */
  category: ManagedChannel | null;
  nodes: readonly ManageNode[];
}>;

export type ManageTree = Readonly<{
  groups: readonly ManageGroup[];
  hiddenCount: number;
}>;

type TreeOptions = Readonly<{ query: string; showNoAccess: boolean }>;

function sorted(channels: readonly ManagedChannel[]): readonly ManagedChannel[] {
  return [...channels].sort(compareChannels);
}

export function buildManageTree(
  channels: readonly ManagedChannel[],
  { query, showNoAccess }: TreeOptions,
): ManageTree {
  const needle = query.trim().toLowerCase();
  const matches = (channel: ManagedChannel) =>
    needle === "" || channel.name.toLowerCase().includes(needle);
  // 已开启但失去权限的频道始终显示。
  const allowed = (channel: ManagedChannel) =>
    showNoAccess || channel.can_read || channel.collect_enabled;

  const byId = new Map(channels.map((channel) => [channel.id, channel]));
  const threadsByParent = new Map<string, ManagedChannel[]>();
  const tops: ManagedChannel[] = [];
  for (const channel of channels) {
    if (channel.type === CATEGORY) continue;
    const parentId = channel.parent_id;
    if (THREAD_TYPES.has(channel.type) && parentId !== null && byId.has(parentId)) {
      threadsByParent.set(parentId, [...(threadsByParent.get(parentId) ?? []), channel]);
    } else {
      tops.push(channel);
    }
  }

  const grouped = new Map<string | null, ManageNode[]>();
  for (const channel of sorted(tops)) {
    const threads = (threadsByParent.get(channel.id) ?? []).filter(
      (thread) => allowed(thread) && matches(thread),
    );
    // 留下来的线程会把父频道一并带上。
    if (threads.length === 0 && !(allowed(channel) && matches(channel))) continue;
    const parent = channel.parent_id === null ? undefined : byId.get(channel.parent_id);
    const key = parent?.type === CATEGORY ? parent.id : null;
    const node = { channel, threads: sorted(threads) };
    grouped.set(key, [...(grouped.get(key) ?? []), node]);
  }

  const categories = sorted(channels.filter((channel) => grouped.has(channel.id)));
  const loose = grouped.get(null);
  return {
    groups: [
      ...(loose ? [{ category: null, nodes: loose }] : []),
      ...categories.map((category) => ({ category, nodes: grouped.get(category.id) ?? [] })),
    ],
    hiddenCount: showNoAccess
      ? 0
      : channels.filter((c) => c.collectable && !c.can_read && !c.collect_enabled).length,
  };
}
