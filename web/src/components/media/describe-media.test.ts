import { describe, expect, it } from "vitest";

import { checkLimits, formatUsedGb, usedPercent } from "./describe-media";

describe("describe-media", () => {
  // covers: spec 0011 AC-8
  it("已用容量 GB 保留一位小数，占比取整数并封顶 100", () => {
    expect(formatUsedGb(1.25 * 1024 ** 3)).toBe("1.3 GB");
    expect(usedPercent(25 * 1024 ** 3, 50)).toBe(50);
    expect(usedPercent(60 * 1024 ** 3, 50)).toBe(100);
    expect(usedPercent(0, 50)).toBe(0);
  });

  // covers: spec 0011 AC-8
  it("两个上限只收范围内的整数", () => {
    expect(checkLimits({ maxFileMb: "50", quotaGb: "50" })).toEqual({
      ok: true,
      maxFileMb: 50,
      quotaGb: 50,
    });
    expect(checkLimits({ maxFileMb: "1024", quotaGb: "10000" }).ok).toBe(true);
    for (const bad of ["0", "1025", "1.5", "", "abc", "-3"]) {
      const check = checkLimits({ maxFileMb: bad, quotaGb: "50" });
      expect(check.ok).toBe(false);
    }
    const both = checkLimits({ maxFileMb: "0", quotaGb: "10001" });
    expect(both).toMatchObject({ ok: false });
    if (!both.ok) {
      expect(both.maxFileMb).toContain("1 到 1024");
      expect(both.quotaGb).toContain("1 到 10000");
    }
  });
});
