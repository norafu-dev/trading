// 导出范围的选择状态（spec 0014）：只在 ChannelView 的组件状态里，不进网址。
// 全部是纯函数，转换规则见 spec 的「选择状态」表。

export type MessageEnd = Readonly<{
  kind: "message";
  id: string;
  createdAt: string; // ISO 8601，UTC
  authorName: string;
}>;
export type OpenEnd = Readonly<{ kind: "open" }>;
export type ExportEnd = MessageEnd | OpenEnd;

export type ExportSide = "start" | "end";

export type ExportSelection =
  | Readonly<{ step: "none" }>
  // 只定下了一端：一条消息，或者一个快捷按钮（此时要记住是哪一边）。
  | Readonly<{ step: "one"; first: MessageEnd }>
  | Readonly<{ step: "one-open"; side: ExportSide }>
  | Readonly<{ step: "ready"; start: ExportEnd; end: ExportEnd }>;

export const NO_SELECTION: ExportSelection = { step: "none" };

const OPEN: OpenEnd = { kind: "open" };

export type MessageLike = Readonly<{ id: string; createdAt: string; authorName: string }>;

export function toEnd(message: MessageLike): MessageEnd {
  return {
    kind: "message",
    id: message.id,
    createdAt: message.createdAt,
    authorName: message.authorName,
  };
}

// 消息 ID 超过 JS 安全整数，按 BigInt 比较。
function isBefore(a: MessageEnd, b: MessageEnd): boolean {
  return BigInt(a.id) < BigInt(b.id);
}

function ordered(a: MessageEnd, b: MessageEnd): Readonly<{ start: MessageEnd; end: MessageEnd }> {
  return isBefore(b, a) ? { start: b, end: a } : { start: a, end: b };
}

function ready(start: ExportEnd, end: ExportEnd): ExportSelection {
  return { step: "ready", start, end };
}

// 点一条消息（AC-2、AC-3）。
export function selectMessage(selection: ExportSelection, message: MessageLike): ExportSelection {
  const picked = toEnd(message);
  switch (selection.step) {
    case "none":
      return { step: "one", first: picked };
    case "one": {
      const { start, end } = ordered(selection.first, picked);
      return ready(start, end);
    }
    case "one-open":
      return selection.side === "start" ? ready(OPEN, picked) : ready(picked, OPEN);
    case "ready": {
      const { start, end } = selection;
      if (start.kind === "message" && end.kind === "message") return { step: "one", first: picked };
      // 有一端开放：替换消息那一端；两端都开放时约定替换终点。
      if (start.kind === "open") return ready(OPEN, picked);
      return ready(picked, OPEN);
    }
  }
}

function oneOpen(side: ExportSide): ExportSelection {
  return { step: "one-open", side };
}

function oneMessage(first: ExportEnd): ExportSelection {
  return first.kind === "message" ? { step: "one", first } : NO_SELECTION;
}

// 点「从最早」（start）或「到最新」（end）；再点亮着的那个就取消它（AC-3）。
export function toggleOpen(selection: ExportSelection, side: ExportSide): ExportSelection {
  switch (selection.step) {
    case "none":
      return oneOpen(side);
    case "one":
      return side === "start" ? ready(OPEN, selection.first) : ready(selection.first, OPEN);
    case "one-open":
      if (selection.side === side) return NO_SELECTION;
      return ready(OPEN, OPEN);
    case "ready": {
      const { start, end } = selection;
      if (start.kind === "open" && end.kind === "open")
        return oneOpen(side === "start" ? "end" : "start");
      if (side === "start") {
        if (start.kind === "open") return oneMessage(end);
        return ready(OPEN, end);
      }
      if (end.kind === "open") return oneMessage(start);
      return ready(start, OPEN);
    }
  }
}

export type RangeParams = Readonly<{ start?: string; end?: string }>;

// 范围确定时给接口的参数；开放的一端不传（AC-6）。
export function toRangeParams(selection: ExportSelection): RangeParams | null {
  if (selection.step !== "ready") return null;
  return {
    ...(selection.start.kind === "message" ? { start: selection.start.id } : {}),
    ...(selection.end.kind === "message" ? { end: selection.end.id } : {}),
  };
}

export type ExportFormat = "csv" | "jsonl";

// 下载网址手动拼：浏览器直接打开它，openapi-fetch 管不到文件下载（AC-10）。
export function buildExportUrl(
  channelId: string,
  range: RangeParams,
  format: ExportFormat,
  timeZone: string,
): string {
  const query = new URLSearchParams();
  if (range.start !== undefined) query.set("start", range.start);
  if (range.end !== undefined) query.set("end", range.end);
  query.set("format", format);
  query.set("tz", timeZone);
  return `/api/channels/${encodeURIComponent(channelId)}/export?${query.toString()}`;
}

// 消息行上的标记（AC-4）。
export type ExportMark = "picked" | "start" | "end" | "single" | "range";

export function markFor(selection: ExportSelection, messageId: string): ExportMark | null {
  if (selection.step === "one") return selection.first.id === messageId ? "picked" : null;
  if (selection.step !== "ready") return null;
  const { start, end } = selection;
  const id = BigInt(messageId);
  const isStart = start.kind === "message" && start.id === messageId;
  const isEnd = end.kind === "message" && end.id === messageId;
  if (isStart && isEnd) return "single";
  if (isStart) return "start";
  if (isEnd) return "end";
  const afterStart = start.kind === "open" || BigInt(start.id) < id;
  const beforeEnd = end.kind === "open" || id < BigInt(end.id);
  return afterStart && beforeEnd ? "range" : null;
}
