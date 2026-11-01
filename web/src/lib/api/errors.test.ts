import { describe, expect, it } from "vitest";

import { ApiResponseError, toUiError } from "./errors";

describe("toUiError", () => {
  // covers: AC-13
  it("5xx 是可以重试的服务器错误，说明里带状态码", () => {
    const error = toUiError(new ApiResponseError(503));
    expect(error).toMatchObject({ kind: "server", title: "服务器出错了", retryable: true });
    expect(error.detail).toContain("503");
  });

  it("401 要重新登录，不能重试", () => {
    expect(toUiError(new ApiResponseError(401))).toMatchObject({
      kind: "unauthorized",
      title: "需要重新登录",
      retryable: false,
    });
  });

  it("404 是找不到内容，不能重试", () => {
    expect(toUiError(new ApiResponseError(404))).toMatchObject({
      kind: "not_found",
      retryable: false,
    });
  });

  it("其他 4xx 是请求失败，带状态码", () => {
    const error = toUiError(new ApiResponseError(422));
    expect(error).toMatchObject({ kind: "unknown", title: "请求失败" });
    expect(error.detail).toContain("422");
  });

  it("fetch 抛出的 TypeError 是连不上服务器，可以重试", () => {
    expect(toUiError(new TypeError("Failed to fetch"))).toMatchObject({
      kind: "network",
      title: "连不上服务器",
      retryable: true,
    });
  });

  it.each([
    ["普通 Error", new Error("boom")],
    ["字符串", "boom"],
    ["undefined", undefined],
  ])("%s 是意外错误，可以重试", (_label, value) => {
    expect(toUiError(value)).toMatchObject({
      kind: "unknown",
      title: "出了点问题",
      retryable: true,
    });
  });
});

describe("ApiResponseError", () => {
  it("记下状态码，并有默认的错误信息", () => {
    const error = new ApiResponseError(500);
    expect(error.status).toBe(500);
    expect(error.message).toBe("request failed (500)");
    expect(error).toBeInstanceOf(Error);
  });
});
