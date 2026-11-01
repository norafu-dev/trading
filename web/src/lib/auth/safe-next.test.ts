import { describe, expect, it } from "vitest";

import { loginHref, safeNextPath } from "./safe-next";

describe("safeNextPath", () => {
  // covers: AC-13
  it.each([
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",
    "/\t/evil.com",
    "evil.com",
    "",
    null,
    undefined,
  ])("%s 回首页", (value) => {
    expect(safeNextPath(value)).toBe("/");
  });

  // covers: AC-13
  it.each(["/", "/channels?id=1", "/settings/security"])("%s 原样保留", (value) => {
    expect(safeNextPath(value)).toBe(value);
  });
});

describe("loginHref", () => {
  // covers: AC-13, AC-14
  it("把当前地址编码进 next，过期时带 reason", () => {
    expect(loginHref("/channels?id=1")).toBe("/login?next=%2Fchannels%3Fid%3D1");
    expect(loginHref("/", "expired")).toBe("/login?next=%2F&reason=expired");
  });
});
