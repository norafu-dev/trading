// 频道页消息缓存的纯函数（spec 0008）。pages[0] 是最新的一页，往后越来越旧。
import type { InfiniteData } from "@tanstack/react-query";

import { compareIds, mergeMessages } from "@/components/messages/merge-messages";
import type { components } from "@/lib/api/schema";

export type MessagesPage = components["schemas"]["MessagesOut"];
// null 是最新一页，字符串是 `before` 游标；`around` 和 `after` 只出现在「以某条为中心」的加载里（spec 0013）。
export type PageParam = string | null | Readonly<{ around: string }> | Readonly<{ after: string }>;
export type MessagesData = InfiniteData<MessagesPage, PageParam>;
type Named = components["schemas"]["NamedRefOut"];

export const pageFrom = (page: MessagesPage): MessagesData => ({
  pages: [page],
  pageParams: [null],
});

// 以某条为中心加载的第一页：之后向新翻页时新的页会插在它前面。
export const aroundFrom = (page: MessagesPage, messageId: string): MessagesData => ({
  pages: [page],
  pageParams: [{ around: messageId }],
});

// 还没加载到最新消息吗？最新的一页（pages[0]）如果是 around 取的，看 `has_newer`；
// 如果是 after 取的，`has_more` 就表示更新的还有。普通加载（最新一页或 before 翻页）永远是 false（AC-19）。
export function hasNewerPages(data: MessagesData): boolean {
  const first = data.pages[0];
  const param = data.pageParams[0];
  if (!first || typeof param !== "object" || param === null) return false;
  return "around" in param ? first.has_newer === true : first.has_more;
}

// 旧的页在前，拼成从旧到新的一条列表。
export function flatten(data: MessagesData): readonly MessagesPage["messages"][number][] {
  return [...data.pages].reverse().flatMap((page) => page.messages);
}

export function newestId(data: MessagesData): string | null {
  return data.pages[0]?.messages.at(-1)?.id ?? null;
}

export function oldestId(data: MessagesData): string | null {
  return data.pages.at(-1)?.messages[0]?.id ?? null;
}

function mergeNamed(existing: readonly Named[], incoming: readonly Named[]): Named[] {
  const byId = new Map(existing.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()];
}

// 已加载的消息就地更新；没有的放进最新一页。所以同一个 ID 不会出现两次（AC-17、AC-18）。
export function mergeIntoPages(data: MessagesData, incoming: MessagesPage): MessagesData {
  const updates = new Map(incoming.messages.map((message) => [message.id, message]));
  const known = new Set(flatten(data).map((message) => message.id));
  const added = incoming.messages.filter((message) => !known.has(message.id));
  const pages = data.pages.map((page, index) => {
    const messages = page.messages.map((message) => updates.get(message.id) ?? message);
    if (index !== 0) return { ...page, messages };
    return {
      ...page,
      messages: [...mergeMessages(messages, added)],
      users: mergeNamed(page.users, incoming.users),
      channels: mergeNamed(page.channels, incoming.channels),
    };
  });
  return { ...data, pages };
}

// 最新一页够不到已有的消息，说明中间有缺口（AC-19）。
export function hasGap(data: MessagesData, latest: MessagesPage): boolean {
  const newest = newestId(data);
  const oldestOfLatest = latest.messages[0]?.id;
  if (newest === null || oldestOfLatest === undefined) return false;
  return compareIds(oldestOfLatest, newest) > 0;
}

export type ChangedPlan = Readonly<{ fetchIds: readonly string[]; needsAfter: boolean }>;

// 比已加载最新的还新：取一次 after；比最早的还旧：忽略；其余就地更新（AC-18）。
export function planChanged(data: MessagesData, ids: Iterable<string>): ChangedPlan {
  const newest = newestId(data);
  const oldest = oldestId(data);
  if (newest === null || oldest === null) return { fetchIds: [], needsAfter: true };
  const list = [...ids];
  return {
    fetchIds: list.filter((id) => compareIds(id, oldest) >= 0 && compareIds(id, newest) <= 0),
    needsAfter: list.some((id) => compareIds(id, newest) > 0),
  };
}

export function chunk<T>(items: readonly T[], size: number): readonly (readonly T[])[] {
  const chunks: T[][] = [];
  for (let index = 0; index < items.length; index += size) {
    chunks.push(items.slice(index, index + size));
  }
  return chunks;
}
