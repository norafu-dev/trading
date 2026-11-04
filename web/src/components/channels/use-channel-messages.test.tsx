import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventStreamProvider } from "@/lib/events/event-stream";

import type { MessagesPage } from "./message-pages";
import { useChannelMessages } from "./use-channel-messages";

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

const CHANNEL = "1";

const row = (id: string, content = "c"): MessagesPage["messages"][number] => ({
  id,
  channel_id: CHANNEL,
  author_id: "2",
  author_name: "a",
  author_avatar_hash: null,
  media: [],
  created_at: "2026-11-02T00:00:00Z",
  content,
  embeds: [],
  forwarded: null,
  attachments: [],
  edited_at: null,
  deleted_at: null,
  reply: null,
});

const page = (ids: readonly string[], has_more = false): MessagesPage => ({
  messages: ids.map((id) => row(id)),
  has_more,
  users: [],
  channels: [],
});

type Query = Readonly<{ before?: string; after?: string; ids?: string; limit?: number }>;
type Handler = (query: Query) => MessagesPage | Promise<MessagesPage>;

function serve(handler: Handler) {
  get.mockImplementation(async (path: string, options: { params: { query?: Query } }) => {
    if (!path.endsWith("/messages")) throw new Error(`unexpected ${path}`);
    return { data: await handler(options.params.query ?? {}), response: { status: 200 } };
  });
}

function setup() {
  const client = new QueryClient();
  const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
    <QueryClientProvider client={client}>
      <EventStreamProvider>{children}</EventStreamProvider>
    </QueryClientProvider>
  );
  return renderHook(() => useChannelMessages(CHANNEL), { wrapper });
}

function emit(name: string, payload: object, channelId = CHANNEL) {
  const data = JSON.stringify({ type: name, channel_id: channelId, ...payload });
  act(() => FakeEventSource.last.listeners.get(name)?.(new MessageEvent(name, { data })));
}

const idsOf = (result: { current: ReturnType<typeof useChannelMessages> }) =>
  result.current.messages.map((m) => m.id);

describe("useChannelMessages", () => {
  beforeEach(() => {
    get.mockReset();
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // covers: spec 0008 AC-16
  it("打开时取最新一页；没有消息时是 empty", async () => {
    serve(() => page([]));
    const { result } = setup();
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("empty"));
  });

  // covers: spec 0008 AC-17
  it("new_message 用 after 取增量并合并", async () => {
    serve((q) => (q.after ? page(["11"]) : page(["10"])));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));

    emit("new_message", { message_id: "11" });

    await waitFor(() => expect(idsOf(result)).toEqual(["10", "11"]));
    expect(get.mock.calls[1]?.[1].params.query.after).toBe("10");
  });

  // covers: spec 0008 AC-17
  it("别的频道的事件不取数", async () => {
    serve(() => page(["10"]));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));
    const calls = get.mock.calls.length;

    emit("new_message", { message_id: "11" }, "999");

    expect(get.mock.calls.length).toBe(calls);
  });

  // covers: spec 0008 AC-17
  it("单飞：取数期间又来通知，结束后只再取一次", async () => {
    let release = () => {};
    const blocked = new Promise<void>((resolve) => {
      release = resolve;
    });
    let afterCalls = 0;
    serve(async (q) => {
      if (!q.after) return page(["10"]);
      afterCalls += 1;
      if (afterCalls === 1) await blocked;
      return page([afterCalls === 1 ? "11" : "12"]);
    });
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));

    emit("new_message", { message_id: "11" });
    emit("new_message", { message_id: "12" });
    emit("new_message", { message_id: "13" });
    expect(afterCalls).toBe(1);
    release();

    await waitFor(() => expect(idsOf(result)).toEqual(["10", "11", "12"]));
    expect(afterCalls).toBe(2);
  });

  // covers: spec 0008 AC-17
  it("after 还有更多时改取最新一页；接不上就替换并回到底部", async () => {
    let first = true;
    serve((q) => {
      if (q.after) return page(["11"], true);
      if (first) {
        first = false;
        return page(["10"], true);
      }
      return page(["90", "91"], true);
    });
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));

    emit("new_message", { message_id: "11" });

    await waitFor(() => expect(idsOf(result)).toEqual(["90", "91"]));
    expect(result.current.epoch).toBe(1);
    expect(result.current.hasOlder).toBe(true);
  });

  // covers: spec 0008 AC-18
  it("message_changed 就地更新；比最新还新的 ID 触发 after；比最早还旧的忽略", async () => {
    serve((q) => {
      if (q.ids) return { ...page([]), messages: [row("10", "edited")] };
      if (q.after) return page(["12"]);
      return page(["10", "11"]);
    });
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10", "11"]));

    emit("message_changed", { message_ids: ["5", "10", "12"] });

    await waitFor(() => expect(idsOf(result)).toEqual(["10", "11", "12"]));
    expect(result.current.messages[0]?.content).toBe("edited");
    const idsCall = get.mock.calls.find((call) => call[1].params.query.ids);
    expect(idsCall?.[1].params.query.ids).toBe("10");
  });

  // covers: spec 0008 AC-19
  it("SSE 重连时重新取最新一页", async () => {
    let round = 0;
    serve(() => (round++ === 0 ? page(["10"]) : page(["10", "11"])));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));

    act(() => FakeEventSource.last.onopen?.());
    expect(round).toBe(1); // 第一次连上不算重连。
    act(() => FakeEventSource.last.onopen?.());

    await waitFor(() => expect(idsOf(result)).toEqual(["10", "11"]));
  });

  // covers: spec 0008 AC-19
  it("空列表拿到第一页时用这一页的 has_more，不增加 epoch", async () => {
    let round = 0;
    serve(() => (round++ === 0 ? page([]) : page(["10"], true)));
    const { result } = setup();
    await waitFor(() => expect(result.current.status).toBe("empty"));

    emit("new_message", { message_id: "10" });

    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));
    expect(result.current.hasOlder).toBe(true);
    expect(result.current.epoch).toBe(0);
  });

  // covers: spec 0008 AC-16
  it("往上翻页取 before，排在前面", async () => {
    serve((q) => (q.before ? page(["8", "9"]) : page(["10"], true)));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));

    act(() => result.current.loadOlder());

    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10"]));
    expect(result.current.hasOlder).toBe(false);
  });

  // covers: spec 0008 AC-16
  it("首次加载失败是 error 状态，可以重试", async () => {
    get.mockResolvedValueOnce({ error: {}, response: { status: 500 } });
    const { result } = setup();
    await waitFor(() => expect(result.current.status).toBe("error"));

    serve(() => page(["10"]));
    act(() => result.current.retry());

    await waitFor(() => expect(result.current.status).toBe("ready"));
  });
});
