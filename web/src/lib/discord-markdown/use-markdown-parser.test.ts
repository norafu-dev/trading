import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initParser } from "./parser";
import { useMarkdownParser } from "./use-markdown-parser";

vi.mock("./parser", () => ({ initParser: vi.fn() }));

const init = vi.mocked(initParser);

// 手动控制 initParser 什么时候完成
function deferred() {
  let resolve: () => void = () => {};
  let reject: (error: unknown) => void = () => {};
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useMarkdownParser", () => {
  // covers: AC-10
  beforeEach(() => {
    vi.useFakeTimers();
    init.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("加载完成前是 pending，完成后是 ready", async () => {
    const load = deferred();
    init.mockReturnValue(load.promise);
    const { result } = renderHook(() => useMarkdownParser());
    expect(result.current).toBe("pending");
    await act(async () => load.resolve());
    expect(result.current).toBe("ready");
  });

  it("3 秒内没就绪就是 failed，之后加载成功也不再切换", async () => {
    const load = deferred();
    init.mockReturnValue(load.promise);
    const { result } = renderHook(() => useMarkdownParser());
    await act(async () => vi.advanceTimersByTime(3000));
    expect(result.current).toBe("failed");
    await act(async () => load.resolve());
    expect(result.current).toBe("failed");
  });

  it("2.9 秒时仍在等待", async () => {
    init.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useMarkdownParser());
    await act(async () => vi.advanceTimersByTime(2900));
    expect(result.current).toBe("pending");
  });

  it("加载失败时是 failed", async () => {
    const load = deferred();
    init.mockReturnValue(load.promise);
    const { result } = renderHook(() => useMarkdownParser());
    await act(async () => load.reject(new Error("wasm blocked")));
    expect(result.current).toBe("failed");
  });
});
