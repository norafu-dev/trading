// 消息时间格式化（spec 0003 的 AC-6 和 `<t:…>` 对照表）。所有函数都是纯函数：
// 时钟（`now`）和时区都从参数传入，这里从不调用 `new Date()` 读当前时间。
//
// 新建一个 Intl 格式器大约 25µs，5 万行加起来要一秒多，所以由 `createTimeFormat`
// 按时区建一次格式器并复用。

export type DiscordTimestampStyle = "t" | "T" | "d" | "D" | "f" | "F" | "R";

type ZonedParts = Readonly<{
  year: number;
  month: number;
  day: number;
  hour: string;
  minute: string;
  second: string;
}>;

export type TimeFormat = Readonly<{
  /** 本地日历日，格式 `YYYY-MM-DD`，用于分组和日期分隔线。 */
  dayKey: (date: Date) => string;
  messageTime: (iso: string, now: Date) => string;
  hoverTime: (iso: string) => string;
  fullTime: (iso: string) => string;
  /** 相对时间，如「2分钟前」；`now` 由调用方传入（页面用 useNow）。 */
  relativeTime: (iso: string, now: Date) => string;
  dayDivider: (dayKey: string) => string;
  discordTimestamp: (seconds: string, style: string | undefined, now: Date) => string | null;
}>;

const STYLES: ReadonlySet<string> = new Set(["t", "T", "d", "D", "f", "F", "R"]);

// 每个单位在取整后的值小于上限时使用，超过就换下一个更大的单位。
const RELATIVE_STEPS: readonly (readonly [Intl.RelativeTimeFormatUnit, number, number])[] = [
  ["second", 1, 60],
  ["minute", 60, 60],
  ["hour", 3600, 24],
  ["day", 86_400, 30],
  ["month", 2_592_000, 12],
  ["year", 31_536_000, Number.POSITIVE_INFINITY],
];

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function keyOf(year: number, month: number, day: number): string {
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

// 在 UTC 里做日历运算，所以夏令时切换那天往前推一天也准确。
function previousDayKey(parts: ZonedParts): string {
  const previous = new Date(Date.UTC(parts.year, parts.month - 1, parts.day - 1));
  return keyOf(previous.getUTCFullYear(), previous.getUTCMonth() + 1, previous.getUTCDate());
}

export function createTimeFormat(timeZone: string): TimeFormat {
  const partsFormat = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const weekdayFormat = new Intl.DateTimeFormat("zh-CN", { timeZone, weekday: "long" });
  const relativeFormat = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" });

  const partsOf = (date: Date): ZonedParts => {
    const found: Record<string, string> = {};
    for (const part of partsFormat.formatToParts(date)) found[part.type] = part.value;
    return {
      year: Number(found.year),
      month: Number(found.month),
      day: Number(found.day),
      hour: found.hour ?? "00",
      minute: found.minute ?? "00",
      second: found.second ?? "00",
    };
  };

  const dayKey = (date: Date): string => {
    const parts = partsOf(date);
    return keyOf(parts.year, parts.month, parts.day);
  };

  const hhmm = (parts: ZonedParts) => `${parts.hour}:${parts.minute}`;
  const longDate = (parts: ZonedParts) => `${parts.year}年${parts.month}月${parts.day}日`;

  const messageTime = (iso: string, now: Date): string => {
    const parts = partsOf(new Date(iso));
    const today = partsOf(now);
    const key = keyOf(parts.year, parts.month, parts.day);
    if (key === keyOf(today.year, today.month, today.day)) return hhmm(parts);
    if (key === previousDayKey(today)) return `昨天 ${hhmm(parts)}`;
    if (parts.year === today.year) return `${parts.month}月${parts.day}日 ${hhmm(parts)}`;
    return `${longDate(parts)} ${hhmm(parts)}`;
  };

  const hoverTime = (iso: string): string => hhmm(partsOf(new Date(iso)));

  const fullTime = (iso: string): string => {
    const date = new Date(iso);
    const parts = partsOf(date);
    return `${longDate(parts)} ${weekdayFormat.format(date)} ${hhmm(parts)}:${parts.second}`;
  };

  const relative = (date: Date, now: Date): string => {
    const diffSeconds = (date.getTime() - now.getTime()) / 1000;
    for (const [unit, seconds, limit] of RELATIVE_STEPS) {
      const value = Math.round(diffSeconds / seconds);
      if (Math.abs(value) < limit) return relativeFormat.format(value, unit);
    }
    return relativeFormat.format(Math.round(diffSeconds / 31_536_000), "year");
  };

  const discordTimestamp = (seconds: string, style: string | undefined, now: Date) => {
    const resolved = style ?? "f";
    if (!/^-?\d+$/.test(seconds) || !STYLES.has(resolved)) return null;
    const date = new Date(Number(seconds) * 1000);
    if (Number.isNaN(date.getTime())) return null;
    const parts = partsOf(date);
    const weekday = weekdayFormat.format(date);
    switch (resolved as DiscordTimestampStyle) {
      case "t":
        return hhmm(parts);
      case "T":
        return `${hhmm(parts)}:${parts.second}`;
      case "d":
        return `${parts.year}/${pad2(parts.month)}/${pad2(parts.day)}`;
      case "D":
        return longDate(parts);
      case "f":
        return `${longDate(parts)} ${hhmm(parts)}`;
      case "F":
        return `${longDate(parts)} ${weekday} ${hhmm(parts)}`;
      case "R":
        return relative(date, now);
    }
  };

  return {
    dayKey,
    messageTime,
    hoverTime,
    fullTime,
    relativeTime: (iso: string, now: Date) => relative(new Date(iso), now),
    dayDivider: formatDayDivider,
    discordTimestamp,
  };
}

export function formatMessageTime(iso: string, now: Date, timeZone: string): string {
  return createTimeFormat(timeZone).messageTime(iso, now);
}

export function formatHoverTime(iso: string, timeZone: string): string {
  return createTimeFormat(timeZone).hoverTime(iso);
}

export function formatFullTime(iso: string, timeZone: string): string {
  return createTimeFormat(timeZone).fullTime(iso);
}

export function formatDayDivider(dayKey: string): string {
  const [year, month, day] = dayKey.split("-").map(Number);
  return `${year}年${month}月${day}日`;
}

export function formatDiscordTimestamp(
  seconds: string,
  style: string | undefined,
  now: Date,
  timeZone: string,
): string | null {
  return createTimeFormat(timeZone).discordTimestamp(seconds, style, now);
}
