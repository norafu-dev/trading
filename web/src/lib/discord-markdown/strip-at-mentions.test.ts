// @vitest-environment node
import { describe, expect, it } from "vitest";

import { stripAtMentions } from "./strip-at-mentions";

describe("stripAtMentions", () => {
  // covers: spec 0015（和后端 test_strip_at_mentions 用同一组例子）
  it.each([
    ["hi <@1> there <#4>", "hi there <#4>"],
    ["<@1> <@!2> <@&3> @everyone @here 早上好", "早上好"],
    ["第一行 <@1>\n<@2>\n  - 缩进 <@3> 保留", "第一行\n  - 缩进 保留"],
    ["mail a@here.com", "mail a@here.com"],
    ["  原样  保留  ", "  原样  保留  "],
    ["<@1>", ""],
  ])("%j → %j", (text, expected) => {
    expect(stripAtMentions(text)).toBe(expected);
  });
});
