import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { parseQuery } from "./parse-query";

type Case = Readonly<{
  q: string;
  include?: readonly string[];
  exclude?: readonly string[];
  error?: string;
  term?: string;
}>;

// 和后端 pytest 共用同一份用例（AC-26）。
const cases = JSON.parse(
  readFileSync(
    join(process.cwd(), "..", "backend", "tests", "fixtures", "search_query_cases.json"),
    "utf-8",
  ),
) as readonly Case[];

describe("parseQuery", () => {
  it.each(cases.map((c) => [c.q.slice(0, 30), c] as const))("共用用例 %s", (_name, c) => {
    const result = parseQuery(c.q);
    if (c.error) {
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.reason).toBe(c.error);
        expect(result.term).toBe(c.term ?? null);
      }
    } else {
      expect(result).toEqual({ ok: true, include: c.include, exclude: c.exclude });
    }
  });
});
