// 关键词解析（spec 0013 AC-3、AC-4）。规则和后端 `harvester.api.search_query.parse_query` 完全一致，
// 两边的单元测试共用 backend/tests/fixtures/search_query_cases.json。

export const MAX_QUERY_CHARS = 200;
export const MAX_TERMS = 10;
export const MIN_TERM_CHARS = 2;

export type QueryReason = "too_long" | "too_many_terms" | "term_too_short" | "exclude_only";

export type ParsedQuery = Readonly<{
  ok: true;
  include: readonly string[];
  exclude: readonly string[];
}>;
export type QueryError = Readonly<{ ok: false; reason: QueryReason; term: string | null }>;

// 和后端共用同一组空白字符（Python 和 JS 的 \s 不完全一样，所以写死）。
const WS = /[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]/;
const WS_RUN = new RegExp(`${WS.source}+`, "g");
const WS_EDGES = new RegExp(`^${WS.source}+|${WS.source}+$`, "g");

type Token = Readonly<{ negate: boolean; term: string }>;

function normalize(raw: string): string {
  return raw.replace(WS_RUN, " ").replace(WS_EDGES, "").toLowerCase();
}

function tokenize(q: string): readonly Token[] {
  const chars = [...q];
  const tokens: Token[] = [];
  let i = 0;
  while (i < chars.length) {
    if (WS.test(chars[i] ?? "")) {
      i += 1;
      continue;
    }
    let negate = false;
    if (chars[i] === "-") {
      negate = true;
      i += 1;
      if (i >= chars.length || WS.test(chars[i] ?? "")) continue; // 单独的 -
    }
    let raw: string;
    if (chars[i] === '"') {
      let end = chars.indexOf('"', i + 1);
      if (end === -1) end = chars.length; // 没闭合：到结尾
      raw = chars.slice(i + 1, end).join("");
      i = Math.min(end + 1, chars.length);
    } else {
      let j = i;
      while (j < chars.length && !WS.test(chars[j] ?? "")) j += 1;
      raw = chars.slice(i, j).join("");
      i = j;
    }
    const term = normalize(raw);
    if (term) tokens.push({ negate, term });
  }
  return tokens;
}

export function parseQuery(q: string): ParsedQuery | QueryError {
  if ([...q].length > MAX_QUERY_CHARS) return { ok: false, reason: "too_long", term: null };
  const seen = new Set<string>();
  const tokens = tokenize(q).filter((t) => {
    const key = `${t.negate ? "-" : "+"}${t.term}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (tokens.length > MAX_TERMS) return { ok: false, reason: "too_many_terms", term: null };
  const short = tokens.find((t) => [...t.term].length < MIN_TERM_CHARS);
  if (short) return { ok: false, reason: "term_too_short", term: short.term };
  return {
    ok: true,
    include: tokens.filter((t) => !t.negate).map((t) => t.term),
    exclude: tokens.filter((t) => t.negate).map((t) => t.term),
  };
}

export const REASON_TEXT: Readonly<Record<QueryReason, string>> = {
  too_long: `关键词最多 ${MAX_QUERY_CHARS} 个字符`,
  too_many_terms: `最多 ${MAX_TERMS} 个词（包括排除词）`,
  term_too_short: `每个词至少 ${MIN_TERM_CHARS} 个字符`,
  exclude_only: "只有排除词时，要同时选择服务器、频道、作者或时间",
};
