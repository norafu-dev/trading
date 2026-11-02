import { describe, expect, it } from "vitest";

import { mergeMessages } from "./merge-messages";

describe("mergeMessages", () => {
  // covers: spec 0008 AC-17
  it("去重，新的覆盖旧的", () => {
    const merged = mergeMessages([{ id: "1", v: "old" }], [{ id: "1", v: "new" }]);
    expect(merged).toEqual([{ id: "1", v: "new" }]);
  });

  // covers: spec 0008 AC-13
  it("按 BigInt 排序：超过安全整数和位数不同都不会排错", () => {
    const ids = ["9007199254740993", "9007199254740992", "100"].map((id) => ({ id }));
    expect(mergeMessages(ids, []).map((m) => m.id)).toEqual([
      "100",
      "9007199254740992",
      "9007199254740993",
    ]);
  });
});
