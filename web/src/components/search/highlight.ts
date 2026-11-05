// 搜索命中词高亮（spec 0013 AC-13）。直接在原文上找，不先转小写再用下标切回原文，
// 因为转小写可能改变字符数。

export type HighlightPart = Readonly<{ text: string; hit: boolean }>;

type Range = readonly [start: number, end: number];

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// 短语里的空格匹配显示文字里任意长度的连续空白。
function patternFor(term: string): RegExp {
  const source = term.split(/\s+/).map(escapeRegExp).join("\\s+");
  return new RegExp(source, "giu");
}

function mergeRanges(ranges: readonly Range[]): readonly Range[] {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged: Range[] = [];
  for (const range of sorted) {
    const last = merged.at(-1);
    if (last && range[0] <= last[1]) {
      merged[merged.length - 1] = [last[0], Math.max(last[1], range[1])];
    } else {
      merged.push(range);
    }
  }
  return merged;
}

/** 把文字按命中词切成片段；多个词重叠时合并成一段。没有词或没有命中时返回整段不高亮。 */
export function splitHighlights(text: string, terms: readonly string[]): readonly HighlightPart[] {
  const usable = terms.filter((term) => term.trim() !== "");
  if (text === "" || usable.length === 0) return [{ text, hit: false }];
  const ranges = usable.flatMap((term) =>
    [...text.matchAll(patternFor(term))].map((m): Range => [m.index, m.index + m[0].length]),
  );
  if (ranges.length === 0) return [{ text, hit: false }];
  const parts: HighlightPart[] = [];
  let cursor = 0;
  for (const [start, end] of mergeRanges(ranges)) {
    if (start > cursor) parts.push({ text: text.slice(cursor, start), hit: false });
    parts.push({ text: text.slice(start, end), hit: true });
    cursor = end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor), hit: false });
  return parts;
}
