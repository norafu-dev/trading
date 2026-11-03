import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse } from "@/components/auth/test-utils";
import { ApiResponseError } from "@/lib/api/errors";

import { useDiscordAccount } from "./use-discord-account";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

function wrapper({ children }: Readonly<{ children: ReactNode }>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useDiscordAccount", () => {
  beforeEach(() => {
    get.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("把信封拆开：没有账号时是 null", async () => {
    get.mockResolvedValue({ data: { account: null }, ...jsonResponse(200) });

    const { result } = renderHook(() => useDiscordAccount(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("接口错误转成统一的 ApiResponseError", async () => {
    get.mockResolvedValue({ error: { error: { code: "x", message: "y" } }, ...jsonResponse(503) });

    const { result } = renderHook(() => useDiscordAccount(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(ApiResponseError);
  });

  // covers: AC-15
  it("没有 SSE 事件时每 30 秒轮询一次", async () => {
    vi.useFakeTimers();
    get.mockResolvedValue({ data: { account: null }, ...jsonResponse(200) });
    renderHook(() => useDiscordAccount(), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(get).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(29_000));
    expect(get).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(get).toHaveBeenCalledTimes(2);
  });
});
