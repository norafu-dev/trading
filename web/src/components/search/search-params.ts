// 搜索条件和网址的互相转换（spec 0013「网址和接口参数」）。规则和后端 `parse_filters` 一致：
// 网址里不合法的值直接丢掉，不报错。

export const MAX_GUILDS = 50;
export const MAX_CHANNELS = 200;
export const MAX_AUTHORS = 20;

export type TimeRange = "today" | "7d" | "30d";

export type SearchFilters = Readonly<{
  q: string;
  guilds: readonly string[];
  channels: readonly string[];
  authors: readonly string[];
  /** 快捷时间；和 from/to 同时出现时以 from/to 为准。 */
  range: TimeRange | null;
  from: string | null;
  to: string | null;
}>;

export const EMPTY_FILTERS: SearchFilters = {
  q: "",
  guilds: [],
  channels: [],
  authors: [],
  range: null,
  from: null,
  to: null,
};

const INT64_MAX = 2n ** 63n - 1n;
const RANGES: readonly TimeRange[] = ["today", "7d", "30d"];

function parseIds(raw: string | null, limit: number): readonly string[] {
  if (!raw) return [];
  const valid = raw.split(",").filter((part) => {
    if (!/^[0-9]{1,19}$/.test(part)) return false;
    const value = BigInt(part);
    return value >= 1n && value <= INT64_MAX;
  });
  return [...new Set(valid)].slice(0, limit);
}

function parseTime(raw: string | null): string | null {
  if (!raw || !/(Z|[+-]\d{2}:\d{2})$/.test(raw)) return null;
  return Number.isNaN(Date.parse(raw)) ? null : raw;
}

export function parseSearchParams(params: URLSearchParams): SearchFilters {
  const range = params.get("range");
  const from = parseTime(params.get("from"));
  const to = parseTime(params.get("to"));
  return {
    q: (params.get("q") ?? "").slice(0, 400),
    guilds: parseIds(params.get("guild"), MAX_GUILDS),
    channels: parseIds(params.get("channel"), MAX_CHANNELS),
    authors: parseIds(params.get("author"), MAX_AUTHORS),
    range: from || to ? null : (RANGES.find((r) => r === range) ?? null),
    from,
    to,
  };
}

export function toSearchParams(filters: SearchFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.guilds.length) params.set("guild", filters.guilds.join(","));
  if (filters.channels.length) params.set("channel", filters.channels.join(","));
  if (filters.authors.length) params.set("author", filters.authors.join(","));
  if (filters.from || filters.to) {
    if (filters.from) params.set("from", filters.from);
    if (filters.to) params.set("to", filters.to);
  } else if (filters.range) {
    params.set("range", filters.range);
  }
  return params;
}

export function hasFilterConditions(filters: SearchFilters): boolean {
  return (
    filters.guilds.length > 0 ||
    filters.channels.length > 0 ||
    filters.authors.length > 0 ||
    filters.range !== null ||
    filters.from !== null ||
    filters.to !== null
  );
}

/** 快捷项换算成固定时间：在第一页请求时做一次，翻页过程中不变（AC-10、AC-15）。 */
export function resolveRange(
  filters: SearchFilters,
  now: Date,
): Readonly<{ from: string | null; to: string | null }> {
  if (filters.from || filters.to) return { from: filters.from, to: filters.to };
  switch (filters.range) {
    case "today": {
      const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      return { from: midnight.toISOString(), to: null };
    }
    case "7d":
      return { from: new Date(now.getTime() - 7 * 24 * 3600 * 1000).toISOString(), to: null };
    case "30d":
      return { from: new Date(now.getTime() - 30 * 24 * 3600 * 1000).toISOString(), to: null };
    default:
      return { from: null, to: null };
  }
}
