import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventStreamProvider } from "@/lib/events/event-stream";

import {
  clearUnread,
  markUnread,
  UnreadProvider,
  useSetCurrentChannel,
  useUnreadChannels,
} from "./unread-provider";

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

function Probe() {
  setCurrent = useSetCurrentChannel();
  return <p>{[...useUnreadChannels()].join(",") || "none"}</p>;
}

function message(channelId: string) {
  const data = JSON.stringify({ type: "new_message", channel_id: channelId, message_id: "9" });
  act(() =>
    FakeEventSource.last.listeners.get("new_message")?.(new MessageEvent("new_message", { data })),
  );
}

describe("UnreadProvider", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <EventStreamProvider>
          <UnreadProvider>
            <Probe />
          </UnreadProvider>
        </EventStreamProvider>
      </QueryClientProvider>,
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  // covers: spec 0008 AC-22
  it("别的频道标未读，当前频道不标；打开时清除", () => {
    act(() => setCurrent("1"));

    message("1");
    expect(screen.getByText("none")).toBeInTheDocument();

    message("2");
    expect(screen.getByText("2")).toBeInTheDocument();

    act(() => setCurrent("2"));
    expect(screen.getByText("none")).toBeInTheDocument();
  });
});

describe("markUnread / clearUnread", () => {
  it("没有变化时返回同一个对象，不触发重新渲染", () => {
    const unread: ReadonlySet<string> = new Set(["1"]);
    expect(markUnread(unread, "1", null)).toBe(unread);
    expect(markUnread(unread, "2", "2")).toBe(unread);
    expect(clearUnread(unread, "2")).toBe(unread);
    expect([...markUnread(unread, "2", null)]).toEqual(["1", "2"]);
  });
});
