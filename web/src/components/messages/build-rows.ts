import { createTimeFormat } from "@/lib/format/time";

import type { MessageView } from "./types";

// 相邻消息间隔小于这个值才合并成一组（spec 0003，AC-5）。
const GROUP_GAP_MS = 7 * 60 * 1000;

export type DayRow = Readonly<{ kind: "day"; key: string; dayKey: string }>;
export type MessageRowItem = Readonly<{
  kind: "message";
  key: string;
  message: MessageView;
  groupStart: boolean;
}>;
export type Row = DayRow | MessageRowItem;

// 日期分隔线和消息放在同一个虚拟列表里。key 是消息 ID（分隔线是 `day:<日期>`），
// 所以在前面插入旧消息后，已有行的 key 和测量缓存都不变。
export function buildRows(messages: readonly MessageView[], timeZone: string): readonly Row[] {
  const format = createTimeFormat(timeZone);
  const rows: Row[] = [];
  let previous: Readonly<{ message: MessageView; time: number; dayKey: string }> | null = null;

  for (const message of messages) {
    const time = Date.parse(message.createdAt);
    const dayKey = format.dayKey(new Date(time));
    const newDay = previous === null || previous.dayKey !== dayKey;
    if (newDay) rows.push({ kind: "day", key: `day:${dayKey}`, dayKey });

    // 已删除的消息照常属于它所在的组，删除本身不会打断分组。
    const groupStart =
      newDay ||
      previous === null ||
      previous.message.authorId !== message.authorId ||
      previous.message.authorName !== message.authorName ||
      message.replyTo !== null ||
      time - previous.time >= GROUP_GAP_MS;

    rows.push({ kind: "message", key: message.id, message, groupStart });
    previous = { message, time, dayKey };
  }
  return rows;
}

function parseId(id: string): bigint | null {
  return /^\d+$/.test(id) ? BigInt(id) : null;
}

export function maxMessageId(messages: readonly MessageView[]): string | null {
  return messages.at(-1)?.id ?? null;
}

// 「上次在底部之后新来的」：末尾 ID 大于当时看到的最大 ID 的消息条数。
// 所以在前面插入旧消息、编辑、删除都不算新消息（AC-7）。
export function countNewMessages(
  messages: readonly MessageView[],
  lastSeenMaxId: string | null,
): number {
  const seen = lastSeenMaxId === null ? null : parseId(lastSeenMaxId);
  if (seen === null) return 0;
  let count = 0;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const id = parseId(messages[index]?.id ?? "");
    if (id === null || id <= seen) break;
    count += 1;
  }
  return count;
}
