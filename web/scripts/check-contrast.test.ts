// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { checkTheme, contrastRatio, themesFromCss } from "./check-contrast.mjs";

const css = readFileSync(fileURLToPath(new URL("../src/app/globals.css", import.meta.url)), "utf8");

describe("对比度检查", () => {
  // covers: AC-14
  it.each(["light", "dark"] as const)("%s 主题的 25 组前景和背景全部达标", (theme) => {
    const tokens = themesFromCss(css)[theme];
    const results = checkTheme(theme, tokens);
    expect(results).toHaveLength(25);
    const failed = results
      .filter((result) => !result.ok)
      .map((result) => `${result.pair.fg}/${result.pair.bg}`);
    expect(failed).toEqual([]);
  });

  it("黑白对比度是 21:1，同色是 1:1", () => {
    const black = { r: 0, g: 0, b: 0, a: 1 };
    const white = { r: 255, g: 255, b: 255, a: 1 };
    expect(contrastRatio(black, white)).toBeCloseTo(21, 5);
    expect(contrastRatio(white, white)).toBeCloseTo(1, 5);
  });

  it("缺少 token 时报错，而不是当成通过", () => {
    expect(() => checkTheme("light", new Map())).toThrow(/缺少 token/);
  });
});
