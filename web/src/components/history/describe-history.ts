import type { components } from "@/lib/api/schema";

export type HistoryJob = components["schemas"]["HistoryJobOut"];
export type HistoryBatch = components["schemas"]["HistoryBatchOut"];
export type HistoryJobPage = components["schemas"]["HistoryJobPageOut"];
export type CreateJobsResult = components["schemas"]["CreateJobsOut"];
export type JobStatus = HistoryJob["status"];
export type SkipReason = components["schemas"]["SkippedChannelOut"]["reason"];

// 每次最多选这么多个频道（spec 0012 AC-1）。
export const MAX_CHANNELS = 50;

export const STATUS_LABELS: Readonly<Record<JobStatus, string>> = {
  pending: "排队中",
  running: "抓取中",
  paused: "已暂停",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const ORIGIN_LABELS: Readonly<Record<HistoryJob["origin"], string>> = {
  manual: "手动",
  gap_manual: "一键补缺口",
  gap_auto: "自动补缺口",
};

export const SKIP_LABELS: Readonly<Record<SkipReason, string>> = {
  not_found: "找不到这个频道",
  channel_deleted: "频道已删除",
  not_collectable: "这种类型的频道不能采集",
  no_access: "小号没有读取权限",
  nothing_to_fetch: "这段时间已经采齐了",
};

export const ERROR_LABELS: Readonly<Record<NonNullable<HistoryJob["error"]>, string>> = {
  no_access: "小号没有读取权限",
  channel_deleted: "频道已删除",
  suspect_empty: "Discord 返回了空结果，但库里这段时间有消息，可能缺少「读取消息历史」权限",
  unexpected: "意外错误，详情见 collector 日志",
};

export const WAIT_LABELS: Readonly<Record<NonNullable<HistoryJob["wait_reason"]>, string>> = {
  rate_limited: "被限速，等待中",
  retrying: "Discord 暂时不可用，稍后重试",
};

export type RangePreset = "24h" | "7d" | "30d" | "all";

export const PRESET_LABELS: Readonly<Record<RangePreset, string>> = {
  "24h": "最近 24 小时",
  "7d": "最近 7 天",
  "30d": "最近 30 天",
  all: "全部历史",
};

const HOUR_MS = 3_600_000;
const PRESET_HOURS: Readonly<Record<Exclude<RangePreset, "all">, number>> = {
  "24h": 24,
  "7d": 24 * 7,
  "30d": 24 * 30,
};

/** `<input type="datetime-local">` 的值，按浏览器本地时区。 */
export function toLocalInput(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

/** 本地时间输入转成带时区的 ISO 时间；空或不合法返回 null。 */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  // 不带时区的日期时间字符串，浏览器按本地时区解析。
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export type RangeInputs = Readonly<{ start: string; end: string; allHistory: boolean }>;

/** 快捷项换成两个输入框的值；「全部历史」的起点由服务端按频道创建时间算。 */
export function presetInputs(preset: RangePreset, now: Date): RangeInputs {
  if (preset === "all") return { start: "", end: "", allHistory: true };
  const start = new Date(now.getTime() - PRESET_HOURS[preset] * HOUR_MS);
  return { start: toLocalInput(start), end: toLocalInput(now), allHistory: false };
}

export type RangeCheck =
  | Readonly<{ ok: true; range_start: string | null; range_end: string | null }>
  | Readonly<{ ok: false; message: string }>;

/** 提交前检查时间段：结束为空表示现在；不是全部历史时起点必填，且要早于终点。 */
export function checkRange(inputs: RangeInputs): RangeCheck {
  const end = localInputToIso(inputs.end);
  if (inputs.end && end === null) return { ok: false, message: "结束时间不正确" };
  if (inputs.allHistory) return { ok: true, range_start: null, range_end: end };
  const start = localInputToIso(inputs.start);
  if (start === null) return { ok: false, message: "请填写开始时间" };
  if (end !== null && start >= end) return { ok: false, message: "开始时间要早于结束时间" };
  return { ok: true, range_start: start, range_end: end };
}

/** 进度百分比，整数。 */
export function percent(progress: number): number {
  return Math.round(Math.min(Math.max(progress, 0), 1) * 100);
}

/** 「进行中」里的行按批次分组，批次按第一行出现的顺序。 */
export function groupByBatch(
  jobs: readonly HistoryJob[],
): readonly Readonly<{ batchId: string; jobs: readonly HistoryJob[] }>[] {
  const groups = new Map<string, HistoryJob[]>();
  for (const job of jobs) {
    const list = groups.get(job.batch_id);
    if (list) list.push(job);
    else groups.set(job.batch_id, [job]);
  }
  return [...groups].map(([batchId, list]) => ({ batchId, jobs: list }));
}

/** 批次标题后面的各状态数量，只列不为 0 的。 */
export function batchStatusSummary(batch: HistoryBatch): string {
  const order: readonly JobStatus[] = [
    "running",
    "pending",
    "paused",
    "failed",
    "done",
    "cancelled",
  ];
  return order
    .filter((s) => batch.status_counts[s] > 0)
    .map((s) => `${STATUS_LABELS[s]} ${batch.status_counts[s]}`)
    .join(" · ");
}

/** 提交后的结果说明。 */
export function describeCreate(result: CreateJobsResult): string {
  if (result.jobs.length === 0) return "没有需要采的时间";
  const channels = new Set(result.jobs.map((j) => j.channel_id)).size;
  return `已为 ${channels} 个频道建了 ${result.jobs.length} 个任务`;
}

type PickerChannel = Readonly<{ name: string; collect_enabled: boolean }>;
export type PickerGuild<C extends PickerChannel> = Readonly<{
  id: string;
  name: string;
  channels: readonly C[];
}>;

/** 搜索服务器名或频道名，不分大小写；空搜索原样返回。没有匹配频道的服务器去掉。 */
export function filterGuilds<C extends PickerChannel>(
  guilds: readonly PickerGuild<C>[],
  query: string,
): readonly PickerGuild<C>[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return guilds;
  return guilds
    .map((g) => ({
      ...g,
      channels: g.name.toLowerCase().includes(needle)
        ? g.channels
        : g.channels.filter((c) => c.name.toLowerCase().includes(needle)),
    }))
    .filter((g) => g.channels.length > 0);
}

/** 拆成「正在采集」和「其他频道」两组，各自仍按服务器分组。 */
export function splitByCollect<C extends PickerChannel>(
  guilds: readonly PickerGuild<C>[],
): Readonly<{ collecting: readonly PickerGuild<C>[]; others: readonly PickerGuild<C>[] }> {
  const pick = (enabled: boolean) =>
    guilds
      .map((g) => ({ ...g, channels: g.channels.filter((c) => c.collect_enabled === enabled) }))
      .filter((g) => g.channels.length > 0);
  return { collecting: pick(true), others: pick(false) };
}

export function countChannels(guilds: readonly PickerGuild<PickerChannel>[]): number {
  return guilds.reduce((sum, g) => sum + g.channels.length, 0);
}
