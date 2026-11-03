import { describe, expect, it } from "vitest";

import { formatBytes } from "./bytes";

describe("formatBytes", () => {
  // covers: AC-11
  it.each([
    [0, "0 B"],
    [1023, "1023 B"],
    [1024, "1.0 KB"],
    [1536, "1.5 KB"],
    [1_572_864, "1.5 MB"],
    [1024 ** 3, "1.0 GB"],
  ])("%d 字节显示为 %s", (size, expected) => {
    expect(formatBytes(size)).toBe(expected);
  });

  it("超过 1024 GB 仍然用 GB，不再进位", () => {
    expect(formatBytes(2048 * 1024 ** 3)).toBe("2048.0 GB");
  });

  it("负数按 0 字节显示", () => {
    expect(formatBytes(-5)).toBe("0 B");
  });
});
