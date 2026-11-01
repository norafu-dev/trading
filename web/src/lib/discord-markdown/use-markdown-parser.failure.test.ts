import { init } from "@discord/markdown-wasm";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@discord/markdown-wasm", () => ({ init: vi.fn(), parse: vi.fn() }));

const wasmInit = vi.mocked(init);

// 这里用真实的 parser.ts 和 hook，只让 WASM 初始化失败，验证整个页面的行为。
describe("WASM 加载失败时", () => {
  // covers: AC-10
  beforeEach(() => {
    // parser.ts 用模块级变量缓存初始化结果，每个测试要拿一份新的模块。
    vi.resetModules();
    wasmInit.mockReset();
    wasmInit.mockRejectedValue(new Error("wasm blocked"));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("多个列表同时挂载，控制台只记一条错误", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    const { useMarkdownParser } = await import("./use-markdown-parser");
    const hooks = Array.from({ length: 5 }, () => renderHook(() => useMarkdownParser()));
    await waitFor(() => {
      for (const { result } of hooks) expect(result.current).toBe("failed");
    });
    expect(errorLog).toHaveBeenCalledTimes(1);
  });

  it("列表重新挂载时不再加载，也不再记错误", async () => {
    const errorLog = vi.spyOn(console, "error").mockImplementation(() => {});
    const { useMarkdownParser } = await import("./use-markdown-parser");
    const first = renderHook(() => useMarkdownParser());
    await waitFor(() => expect(first.result.current).toBe("failed"));
    first.unmount();

    const second = renderHook(() => useMarkdownParser());
    await waitFor(() => expect(second.result.current).toBe("failed"));
    expect(wasmInit).toHaveBeenCalledTimes(1);
    expect(errorLog).toHaveBeenCalledTimes(1);
  });
});
