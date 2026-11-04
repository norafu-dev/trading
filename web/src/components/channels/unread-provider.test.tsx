import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventStreamProvider } from "@/lib/events/event-stream";

import {
  hideCurrent,
  NEW_MESSAGE_COALESCE_MS,
  UnreadProvider,
  useSetCurrentChannel,
  useUnreadCounts,
} from "./unread-provider";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

type Listener = (event: MessageEvent) => void;

class FakeEventSource {
  static last: FakeEventSource;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly listeners = new Map<string, Listener>();

  constructor() {
    FakeEventSource.last = this;
  }

  addEventListener(name: string, listener: Listener) {
    this.listeners.set(name, listener);
  }

  close() {}
}

let setCurrent: (id: string | null) => void = () => {};
let serverUnread: { channel_id: string; count: number }[] = [];

function Probe() {
  setCurrent = useSetCurrentChannel();
  const text = [...useUnreadCounts()].map(([id, n]) => `${id}:${n}`).join(",");
  return <p>{text || "none"}</p>;
}

function emitMessage(channelId: string) {
  const data = JSON.stringify({ type: "new_message", channel_id: channelId, message_id: "9" });
  act(() =>
    FakeEventSource.last.listeners.get("new_message")?.(new MessageEvent("new_message", { data })),
  );
}

const unreadCalls = () => get.mock.calls.length;

describe("UnreadProvider", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serverUnread = [];
    get.mockReset();
    get.mockImplementation(async () => ({
      data: { channels: serverUnread },
      response: new Response(null, { status: 200 }),
    }));
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  const mount = () =>
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EventStreamProvider>
          <UnreadProvider>
            <Probe />
          </UnreadProvider>
        </EventStreamProvider>
      </QueryClientProvider>,
    );

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // covers: spec 0010 AC-5, AC-9
  it("未读只来自接口", async () => {
    serverUnread = [{ channel_id: "2", count: 4 }];
    mount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("2:4")).toBeInTheDocument();
  });

  // covers: spec 0010 AC-7
  it("1 秒内的多条新消息合并成一次重新取数", async () => {
    mount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    const before = unreadCalls();

    emitMessage("2");
    emitMessage("2");
    emitMessage("3");
    await act(() => vi.advanceTimersByTimeAsync(NEW_MESSAGE_COALESCE_MS));

    expect(unreadCalls() - before).toBe(1);
  });

  // covers: spec 0010 AC-7
  it("断线重连后马上重新取数，断线期间到达的消息显示出来", async () => {
    mount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    const before = unreadCalls();
    serverUnread = [{ channel_id: "5", count: 2 }];

    act(() => {
      FakeEventSource.last.onopen?.();
      FakeEventSource.last.onopen?.();
    });
    await act(() => vi.advanceTimersByTimeAsync(0));

    expect(unreadCalls()).toBeGreaterThan(before);
    expect(screen.getByText("5:2")).toBeInTheDocument();
  });

  // covers: spec 0010 AC-7
  it("channels_changed 也会重新取数", async () => {
    mount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    const before = unreadCalls();

    act(() =>
      FakeEventSource.last.listeners.get("channels_changed")?.(
        new MessageEvent("channels_changed"),
      ),
    );
    await act(() => vi.advanceTimersByTimeAsync(0));

    expect(unreadCalls()).toBeGreaterThan(before);
  });

  // covers: spec 0010 AC-6
  it("当前频道且标签页可见时按 0 显示", async () => {
    serverUnread = [
      { channel_id: "1", count: 1 },
      { channel_id: "2", count: 2 },
    ];
    mount();
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("1:1,2:2")).toBeInTheDocument();

    act(() => setCurrent("1"));
    expect(screen.getByText("2:2")).toBeInTheDocument();
  });
});

describe("hideCurrent", () => {
  const counts = new Map([["1", 3]]);

  it("没有变化时返回同一个对象", () => {
    expect(hideCurrent(counts, null, true)).toBe(counts);
    expect(hideCurrent(counts, "1", false)).toBe(counts);
    expect(hideCurrent(counts, "2", true)).toBe(counts);
    expect(hideCurrent(counts, "1", true).size).toBe(0);
  });
});
