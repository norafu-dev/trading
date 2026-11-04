import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DISCORD_ACCOUNT_KEY,
  EventStreamProvider,
  type MessageStreamEvent,
  useEventStream,
  useMessageEvents,
} from "./event-stream";

type Listener = (event: MessageEvent) => void;

class FakeEventSource {
  static last: FakeEventSource | null = null;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly listeners = new Map<string, Listener>();

  constructor(readonly url: string) {
    FakeEventSource.last = this;
  }

  addEventListener(name: string, listener: Listener) {
    this.listeners.set(name, listener);
  }

  emit(name: string, data: string) {
    this.listeners.get(name)?.(new MessageEvent(name, { data }));
  }

  close() {
    this.closed = true;
  }
}

function Probe() {
  const { connected, lastHeartbeat } = useEventStream();
  return <p>{`${connected ? "connected" : "closed"} ${lastHeartbeat ?? "none"}`}</p>;
}

const RECEIVED: MessageStreamEvent[] = [];

function MessageProbe() {
  useMessageEvents((event) => RECEIVED.push(event));
  return null;
}

function setup() {
  const client = new QueryClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <EventStreamProvider>
        <Probe />
        <MessageProbe />
      </EventStreamProvider>
    </QueryClientProvider>,
  );
  const source = FakeEventSource.last;
  if (!source) throw new Error("没有建立 SSE 连接");
  return { invalidate, source, view };
}

describe("EventStreamProvider", () => {
  beforeEach(() => vi.stubGlobal("EventSource", FakeEventSource));
  afterEach(() => vi.unstubAllGlobals());

  // covers: spec 0006 AC-12
  it("channels_changed 事件让频道查询失效", () => {
    const { invalidate, source } = setup();

    act(() => source.emit("channels_changed", "{}"));

    expect(invalidate.mock.calls.map((call) => call[0]?.queryKey)).toEqual([
      ["discord-guilds"],
      ["discord-guild-channels"],
      ["discord-collected"],
      ["discord-channel-detail"],
      ["discord-unread"],
    ]);
  });

  // covers: AC-15
  it("account_status 事件让账号查询失效", () => {
    const { invalidate, source } = setup();

    act(() => source.emit("account_status", "{}"));

    expect(invalidate).toHaveBeenCalledWith({ queryKey: DISCORD_ACCOUNT_KEY });
  });

  // covers: AC-15
  it("重新连上时也补一次", () => {
    const { invalidate, source } = setup();

    act(() => source.onopen?.());

    expect(invalidate).toHaveBeenCalledWith({ queryKey: DISCORD_ACCOUNT_KEY });
    expect(screen.getByText("connected none")).toBeInTheDocument();
  });

  // covers: spec 0008 AC-15, AC-19
  it("转发消息事件，大 ID 不丢精度；只有重连才发 reconnected", () => {
    RECEIVED.length = 0;
    const { source } = setup();

    act(() => source.onopen?.());
    act(() =>
      source.emit(
        "new_message",
        '{"type":"new_message","channel_id":"1","message_id":"9007199254740993"}',
      ),
    );
    act(() => source.onopen?.());

    expect(RECEIVED).toEqual([
      { type: "new_message", channel_id: "1", message_id: "9007199254740993" },
      { type: "reconnected" },
    ]);
  });

  it("记录心跳，卸载时关闭连接", () => {
    const { source, view } = setup();

    act(() => source.emit("heartbeat", JSON.stringify({ server_time: "2026-11-01T10:00:00Z" })));
    expect(screen.getByText("closed 2026-11-01T10:00:00Z")).toBeInTheDocument();

    view.unmount();
    expect(source.closed).toBe(true);
  });
});
