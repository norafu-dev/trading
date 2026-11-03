// 把一个服务器的频道整理成侧栏的分组（spec 0003，AC-2）。纯函数：输入频道行，输出树。

const CATEGORY = 4;
const THREAD_TYPES: ReadonlySet<number> = new Set([10, 11, 12]);

export type ChannelInput = Readonly<{
  id: string;
  guildId: string;
  parentId: string | null;
  type: number;
  name: string;
  position: number | null;
  collectEnabled: boolean;
  status: "active" | "archived" | "deleted";
}>;

export type ChannelNode = Readonly<{
  id: string;
  name: string;
  type: number;
  /** 只为挂住已采集线程而出现的父频道为 false，显示成灰色、不可点。 */
  selectable: boolean;
  threads: readonly ChannelNode[];
}>;

export type ChannelGroup = Readonly<{
  /** 不属于任何分类的频道为 null，这一组排在最前面。 */
  categoryId: string | null;
  name: string | null;
  channels: readonly ChannelNode[];
}>;

type Sortable = Readonly<{ position: number | null; name: string; id: string }>;

// 按 position 升序，没有 position 的排最后，再按名字。
export function compareChannels(a: Sortable, b: Sortable): number {
  if (a.position !== b.position) {
    if (a.position === null) return 1;
    if (b.position === null) return -1;
    return a.position - b.position;
  }
  return a.name.localeCompare(b.name, "zh-CN") || a.id.localeCompare(b.id);
}

function isVisible(channel: ChannelInput): boolean {
  return channel.collectEnabled && channel.status !== "deleted";
}

export function buildChannelTree(channels: readonly ChannelInput[]): readonly ChannelGroup[] {
  const byId = new Map(channels.map((channel) => [channel.id, channel]));
  const visible = channels.filter(isVisible);

  // 父频道已知时，线程挂在父频道下面。
  const threadsByParent = new Map<string, ChannelInput[]>();
  const topLevel = new Map<string, ChannelInput>();
  for (const channel of visible) {
    const parent = channel.parentId === null ? undefined : byId.get(channel.parentId);
    if (THREAD_TYPES.has(channel.type) && parent !== undefined) {
      threadsByParent.set(parent.id, [...(threadsByParent.get(parent.id) ?? []), channel]);
      topLevel.set(parent.id, parent);
    } else {
      topLevel.set(channel.id, channel);
    }
  }

  const toNode = (channel: ChannelInput): ChannelNode => ({
    id: channel.id,
    name: channel.name,
    type: channel.type,
    selectable: isVisible(channel),
    threads: [...(threadsByParent.get(channel.id) ?? [])].sort(compareChannels).map((thread) => ({
      id: thread.id,
      name: thread.name,
      type: thread.type,
      selectable: true,
      threads: [],
    })),
  });

  const grouped = new Map<string | null, ChannelInput[]>();
  for (const channel of topLevel.values()) {
    const category = channel.parentId === null ? undefined : byId.get(channel.parentId);
    const key = category?.type === CATEGORY ? category.id : null;
    grouped.set(key, [...(grouped.get(key) ?? []), channel]);
  }

  const categories = [...grouped.keys()]
    .filter((key): key is string => key !== null)
    .map((key) => byId.get(key))
    .filter((category): category is ChannelInput => category !== undefined)
    .sort(compareChannels);

  const groups: ChannelGroup[] = [];
  const loose = grouped.get(null);
  if (loose) {
    groups.push({
      categoryId: null,
      name: null,
      channels: loose.sort(compareChannels).map(toNode),
    });
  }
  for (const category of categories) {
    const members = grouped.get(category.id) ?? [];
    groups.push({
      categoryId: category.id,
      name: category.name,
      channels: members.sort(compareChannels).map(toNode),
    });
  }
  return groups;
}
