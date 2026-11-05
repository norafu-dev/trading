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

// ---- 任务操作（spec 0012 AC-9） ---------------------------------------------------------

export type JobAction = "pause" | "resume" | "cancel" | "retry";
export type BatchAction = Exclude<JobAction, "retry">;

export const ACTION_LABELS: Readonly<Record<JobAction, string>> = {
  pause: "暂停",
  resume: "继续",
  cancel: "取消",
  retry: "重试",
};

const JOB_ACTIONS: Readonly<Record<JobStatus, readonly JobAction[]>> = {
  pending: ["pause", "cancel"],
  running: ["pause", "cancel"],
  paused: ["resume", "cancel"],
  failed: ["retry", "cancel"],
  done: [],
  cancelled: [],
};

/** 这个状态下能点的操作，和服务端的状态表一致。 */
export function jobActions(status: JobStatus): readonly JobAction[] {
  return JOB_ACTIONS[status];
}

/** 整批能点的操作：只列至少有一行符合的；服务端只改状态符合的行。 */
export function batchActions(batch: HistoryBatch): readonly BatchAction[] {
  const c = batch.status_counts;
  const actions: BatchAction[] = [];
  if (c.pending + c.running > 0) actions.push("pause");
  if (c.paused > 0) actions.push("resume");
  if (c.pending + c.running + c.paused + c.failed > 0) actions.push("cancel");
  return actions;
}

/** 「已结束」列表的时间范围：默认最近 7 天，也可以看保留期内（30 天）的全部。 */
export type FinishedRange = "week" | "all";
export const FINISHED_RANGE_DAYS: Readonly<Record<FinishedRange, number>> = { week: 7, all: 30 };
export const FINISHED_RANGE_LABELS: Readonly<Record<FinishedRange, string>> = {
  week: "最近 7 天",
  all: "全部（保留 30 天）",
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

// ---- 按频道合并子任务（spec 0012 AC-14） -------------------------------------------------

export type ChannelGroup = Readonly<{
  channelId: string;
  guildName: string;
  channelName: string;
  jobs: readonly HistoryJob[];
  /** 最需要你注意的状态：失败 > 抓取中 > 已暂停 > 排队中 > 已取消 > 已完成。 */
  status: JobStatus;
  /** 按时间长度加权：几秒的小段几乎不影响总进度。0 到 1。 */
  progress: number;
  fetchedCount: number;
  rangeStart: string;
  rangeEnd: string;
  counts: Readonly<Record<JobStatus, number>>;
  /** 第一个失败子任务的原因。 */
  error: HistoryJob["error"];
  /** 正在抓的那一段的等待原因和预计继续时间。 */
  waiting: Readonly<{ reason: NonNullable<HistoryJob["wait_reason"]>; until: string }> | null;
  /** 正在抓的那一段已经抓到哪里。 */
  cursorAt: string | null;
}>;

const STATUS_PRIORITY: readonly JobStatus[] = [
  "failed",
  "running",
  "paused",
  "pending",
  "cancelled",
  "done",
];

function emptyCounts(): Record<JobStatus, number> {
  return { pending: 0, running: 0, paused: 0, done: 0, failed: 0, cancelled: 0 };
}

function summarize(channelId: string, jobs: readonly HistoryJob[]): ChannelGroup {
  const counts = emptyCounts();
  let total = 0;
  let covered = 0;
  let fetched = 0;
  for (const job of jobs) {
    counts[job.status] += 1;
    const length = Date.parse(job.range_end) - Date.parse(job.range_start);
    total += length;
    covered += length * job.progress;
    fetched += job.fetched_count;
  }
  const first = jobs[0] as HistoryJob;
  const running = jobs.find((j) => j.status === "running");
  return {
    channelId,
    guildName: first.guild_name,
    channelName: first.channel_name,
    jobs,
    status: STATUS_PRIORITY.find((s) => counts[s] > 0) ?? first.status,
    progress: total > 0 ? covered / total : 0,
    fetchedCount: fetched,
    rangeStart: first.range_start,
    rangeEnd:
      [...jobs]
        .map((j) => j.range_end)
        .sort()
        .at(-1) ?? first.range_end,
    counts,
    error: jobs.find((j) => j.status === "failed")?.error ?? null,
    waiting:
      running?.wait_reason && running.wait_until
        ? { reason: running.wait_reason, until: running.wait_until }
        : null,
    cursorAt: running?.cursor_at ?? null,
  };
}

/** 一个批次里的任务按频道合并，频道按第一次出现的顺序；子任务按时间从早到晚。 */
export function groupByChannel(jobs: readonly HistoryJob[]): readonly ChannelGroup[] {
  const groups = new Map<string, HistoryJob[]>();
  for (const job of jobs) {
    const list = groups.get(job.channel_id);
    if (list) list.push(job);
    else groups.set(job.channel_id, [job]);
  }
  return [...groups].map(([id, list]) =>
    summarize(
      id,
      [...list].sort((a, b) => a.range_start.localeCompare(b.range_start)),
    ),
  );
}

/** 频道这一行能点的操作：只列至少有一个子任务符合的；重试在展开后的子任务上点。 */
export function channelActions(group: ChannelGroup): readonly BatchAction[] {
  const c = group.counts;
  const actions: BatchAction[] = [];
  if (c.pending + c.running > 0) actions.push("pause");
  if (c.paused > 0) actions.push("resume");
  if (c.pending + c.running + c.paused + c.failed > 0) actions.push("cancel");
  return actions;
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

// ---- 总开关、节奏设置（spec 0012 AC-6、AC-8、AC-19） ----------------------------------

export type HistoryStatus = components["schemas"]["HistoryStatusOut"];
export type HistorySettings = components["schemas"]["HistorySettingsOut"];
export type PausedReason = NonNullable<HistoryStatus["paused_reason"]>;

export const PAUSED_LABELS: Readonly<Record<PausedReason, string>> = {
  manual: "你手动暂停",
  rate_limited: "因限速自动暂停",
  account_risk: "账号可能有风险，已自动暂停",
};

/** 图标栏红点的 `aria-label`（AC-7）。 */
export const RATE_LIMITED_LABEL = "历史采集已因限速暂停";

export type TimeText = (iso: string) => string;
export type Notice = Readonly<{ key: string; tone: "info" | "error"; text: string }>;

/** 是不是因为限速自动暂停（红点和页面顶部提示用）。 */
export function isRateLimitedPause(status: HistoryStatus | undefined): boolean {
  return status?.paused_reason === "rate_limited";
}

/** 总开关暂停的文字：原因加上暂停时间。 */
export function pausedText(status: HistoryStatus, fullTime: TimeText): string | null {
  if (status.paused_reason === null) return null;
  const label = PAUSED_LABELS[status.paused_reason];
  return status.paused_at ? `${label}（${fullTime(status.paused_at)}）` : label;
}

/** 整体休息或等限额的文字：原因加上最早可能继续的时间（AC-6）。 */
export function waitingText(status: HistoryStatus, fullTime: TimeText): string | null {
  if (status.waiting_reason === "rest" && status.waiting_until) {
    return `休息中，${fullTime(status.waiting_until)} 继续`;
  }
  const quotaAt =
    status.waiting_reason === "quota" ? status.waiting_until : status.quota_resumes_at;
  return quotaAt ? `今日额度已用完，最早 ${fullTime(quotaAt)} 继续` : null;
}

/** 历史页顶部状态条要显示的提示，按重要程度排。 */
export function statusNotices(status: HistoryStatus, fullTime: TimeText): readonly Notice[] {
  const notices: Notice[] = [];
  const paused = pausedText(status, fullTime);
  if (paused) {
    notices.push({
      key: "paused",
      tone: status.paused_reason === "manual" ? "info" : "error",
      text: paused,
    });
  }
  const waiting = waitingText(status, fullTime);
  if (waiting && !paused) notices.push({ key: "waiting", tone: "info", text: waiting });
  if (status.stale) {
    notices.push({ key: "stale", tone: "error", text: "历史采集任务无响应，请检查 collector" });
  }
  return notices;
}

export const SETTING_FIELDS = [
  { key: "page_delay_min_s", label: "每页最短间隔（秒）", min: 1, max: 120 },
  { key: "page_delay_max_s", label: "每页最长间隔（秒）", min: 1, max: 120 },
  { key: "daily_page_limit", label: "每 24 小时页数上限", min: 50, max: 20000 },
  { key: "run_minutes", label: "连续抓取多久后休息（分钟）", min: 5, max: 240 },
  { key: "rest_minutes", label: "每次休息多久（分钟）", min: 1, max: 120 },
  { key: "auto_gap_max_hours", label: "自动补缺口的时长上限（小时，0 表示关闭）", min: 0, max: 72 },
] as const;

export type SettingKey = (typeof SETTING_FIELDS)[number]["key"];
export type SettingsInput = Readonly<Record<SettingKey, string>>;
export type SettingsValues = Readonly<Record<SettingKey, number>>;
export type SettingsCheck =
  | Readonly<{ ok: true; values: SettingsValues }>
  | Readonly<{ ok: false; errors: Readonly<Partial<Record<SettingKey, string>>> }>;

/** 六个数值都是范围内的整数，最短间隔不大于最长间隔；不通过时不保存并说明（AC-19）。 */
export function checkSettings(input: SettingsInput): SettingsCheck {
  const values: Partial<Record<SettingKey, number>> = {};
  const errors: Partial<Record<SettingKey, string>> = {};
  for (const { key, min, max } of SETTING_FIELDS) {
    const text = input[key].trim();
    const value = Number(text);
    if (!/^\d+$/.test(text) || value < min || value > max) {
      errors[key] = `请输入 ${min} 到 ${max} 之间的整数`;
    } else {
      values[key] = value;
    }
  }
  const lo = values.page_delay_min_s;
  const hi = values.page_delay_max_s;
  if (lo !== undefined && hi !== undefined && lo > hi) {
    errors.page_delay_min_s = "最短间隔不能大于最长间隔";
  }
  if (Object.keys(errors).length > 0) return { ok: false, errors };
  return { ok: true, values: values as SettingsValues };
}
