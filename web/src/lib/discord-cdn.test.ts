import { describe, expect, it } from "vitest";

import { avatarUrl, emojiUrl, fallbackColorFor, guildIconUrl, initialFor } from "./discord-cdn";

describe("avatarUrl", () => {
  // covers: AC-12
  it("a_ 开头的 hash 是动图，用 gif", () => {
    expect(avatarUrl("123", "a_abc")).toBe(
      "https://cdn.discordapp.com/avatars/123/a_abc.gif?size=80",
    );
  });

  it("普通 hash 用 webp", () => {
    expect(avatarUrl("123", "abc")).toBe("https://cdn.discordapp.com/avatars/123/abc.webp?size=80");
  });

  it("没有 hash 时返回 null，由界面显示首字", () => {
    expect(avatarUrl("123", null)).toBeNull();
    expect(avatarUrl("123", "")).toBeNull();
  });
});

describe("guildIconUrl", () => {
  // covers: AC-12
  it("a_ 开头的 hash 以 .gif?size=96 结尾", () => {
    expect(guildIconUrl("9", "a_x")).toMatch(/\/icons\/9\/a_x\.gif\?size=96$/);
  });

  it("普通 hash 用 webp", () => {
    expect(guildIconUrl("9", "x")).toMatch(/\/icons\/9\/x\.webp\?size=96$/);
  });

  it("没有 hash 时返回 null", () => {
    expect(guildIconUrl("9", null)).toBeNull();
  });
});

describe("emojiUrl", () => {
  // covers: AC-9
  it("动态表情请求 gif，静态表情请求 webp", () => {
    expect(emojiUrl("42", true)).toBe("https://cdn.discordapp.com/emojis/42.gif?size=48");
    expect(emojiUrl("42", false)).toBe("https://cdn.discordapp.com/emojis/42.webp?size=48");
  });
});

describe("initialFor", () => {
  // covers: AC-12
  it.each([
    ["中文名", "坏头像同学", "坏"],
    ["英文名转成大写", "alice", "A"],
    ["emoji 开头", "🎮 游戏", "🎮"],
    ["由多个码点组成的 emoji 算一个字", "👨‍👩‍👧 一家人", "👨‍👩‍👧"],
    ["前面有空格", "  bob", "B"],
  ])("%s取第一个字形", (_label, name, expected) => {
    expect(initialFor(name)).toBe(expected);
  });

  it("空名字和只有空格的名字显示 ?", () => {
    expect(initialFor("")).toBe("?");
    expect(initialFor("   ")).toBe("?");
  });
});

describe("fallbackColorFor", () => {
  // covers: AC-12
  it("同一个 ID 每次得到同一种底色", () => {
    const id = "1234567890123456789";
    expect(fallbackColorFor(id)).toBe(fallbackColorFor(id));
  });

  it("超过 JS 安全整数的雪花 ID 也按精确值取余", () => {
    // 2^63 - 1 对 6 取余是 1；用 Number 计算会因精度丢失得到别的值
    expect(fallbackColorFor("9223372036854775807")).toBe("bg-avatar-2");
  });

  it("不同 ID 会分到六种底色", () => {
    const colors = new Set(["0", "1", "2", "3", "4", "5"].map(fallbackColorFor));
    expect(colors.size).toBe(6);
  });

  it("不是数字的 ID 用第一种底色", () => {
    expect(fallbackColorFor("webhook-x")).toBe("bg-avatar-1");
  });
});
