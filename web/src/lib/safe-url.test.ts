import { describe, expect, it } from "vitest";

import { hostDiffers, looksLikeUrl, safeUrl } from "./safe-url";

describe("safeUrl", () => {
  // covers: AC-9
  it("放行 https 和 http 地址", () => {
    expect(safeUrl("https://example.com/a?b=1")?.href).toBe("https://example.com/a?b=1");
    expect(safeUrl("http://example.com")?.hostname).toBe("example.com");
  });

  it.each([
    ["javascript:", "javascript:alert(1)"],
    ["大小写混写的 javascript:", "JaVaScRiPt:alert(1)"],
    ["data:", "data:text/html,<script>alert(1)</script>"],
    ["ftp:", "ftp://example.com/file"],
    ["不完整的地址", "example.com"],
    ["空字符串", ""],
  ])("拒绝 %s", (_label, raw) => {
    expect(safeUrl(raw)).toBeNull();
  });

  it("null 和 undefined 返回 null", () => {
    expect(safeUrl(null)).toBeNull();
    expect(safeUrl(undefined)).toBeNull();
  });
});

describe("hostDiffers", () => {
  // covers: AC-9
  it("链接文字是一个域名、实际指向另一个域名时判定为不同", () => {
    expect(hostDiffers("https://a.com", new URL("https://evil.com"))).toBe(true);
  });

  it("文字不带协议时也能比较域名", () => {
    expect(hostDiffers("bank.example.com", new URL("https://evil.example.net"))).toBe(true);
  });

  it("域名相同时不判定为不同，忽略大小写和路径", () => {
    expect(hostDiffers("Example.COM/docs", new URL("https://example.com/other"))).toBe(false);
  });

  // 调用方先用 looksLikeUrl 过滤，这里只保证解析失败时不误报
  it("文字解析不出域名时不判定为不同", () => {
    expect(hostDiffers("hello world", new URL("https://example.com"))).toBe(false);
  });
});

describe("looksLikeUrl", () => {
  it.each(["https://example.com", "example.com/path", "sub.example.co.uk:8080"])(
    "%s 看起来像网址",
    (text) => {
      expect(looksLikeUrl(text)).toBe(true);
    },
  );

  it.each(["点我", "hello world", "v1.2"])("%s 不像网址", (text) => {
    expect(looksLikeUrl(text)).toBe(false);
  });
});
