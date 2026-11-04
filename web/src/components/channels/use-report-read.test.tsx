import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UNREAD_KEY } from "@/lib/events/event-stream";

import { REPORT_DEBOUNCE_MS, useReportRead } from "./use-report-read";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { POST: post } }));

let client: QueryClient;
const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

const ok = () => ({ response: new Response(null, { status: 200 }) });
const wait = () => act(() => vi.advanceTimersByTimeAsync(REPORT_DEBOUNCE_MS));

describe("useReportRead", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    post.mockReset();
    post.mockImplementation(async () => ok());
    client = new QueryClient();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });
  afterEach(() => vi.useRealTimers());

  // covers: spec 0010 AC-6
  it("可见且消息已加载时上报最新一条的 ID，新消息到了再上报", async () => {
    const { rerender } = renderHook(({ id }) => useReportRead("1", id, true), {
      wrapper,
      initialProps: { id: "10" },
    });
    await wait();
    expect(post).toHaveBeenLastCalledWith("/api/discord/channels/{channel_id}/read", {
      params: { path: { channel_id: "1" } },
      body: { message_id: "10" },
    });

    rerender({ id: "11" });
    await wait();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.lastCall?.[1].body.message_id).toBe("11");
  });

  // covers: spec 0010 AC-6
  it("还没加载好时不上报", async () => {
    renderHook(() => useReportRead("1", "10", false), { wrapper });
    await wait();
    expect(post).not.toHaveBeenCalled();
  });

  // covers: spec 0010 AC-6
  it("标签页在后台时不上报，重新可见时上报一次", async () => {
    setVisibility("hidden");
    renderHook(() => useReportRead("1", "10", true), { wrapper });
    await wait();
    expect(post).not.toHaveBeenCalled();

    setVisibility("visible");
    await wait();
    expect(post).toHaveBeenCalledTimes(1);
  });

  // covers: spec 0010 AC-8
  it("上报成功后立刻把该频道从未读缓存里去掉", async () => {
    client.setQueryData(
      UNREAD_KEY,
      new Map([
        ["1", 3],
        ["2", 1],
      ]),
    );
    renderHook(() => useReportRead("1", "10", true), { wrapper });
    await wait();

    expect(client.getQueryData(UNREAD_KEY)).toEqual(new Map([["2", 1]]));
  });

  // covers: spec 0010 AC-8
  it("上报失败时保持未读，不抛错", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    post.mockImplementation(async () => ({ response: new Response(null, { status: 500 }) }));
    client.setQueryData(UNREAD_KEY, new Map([["1", 3]]));
    renderHook(() => useReportRead("1", "10", true), { wrapper });
    await wait();

    expect(client.getQueryData(UNREAD_KEY)).toEqual(new Map([["1", 3]]));
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});
