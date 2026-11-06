import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EventStreamProvider } from "@/lib/events/event-stream";

import type { MessagesPage } from "./message-pages";
import { HISTORY_COALESCE_MS, useChannelMessages } from "./use-channel-messages";

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

const page = (
  ids: readonly string[],
  has_more = false,
  has_newer: boolean | null = null,
): MessagesPage => ({
  messages: ids.map((id) => row(id)),
  has_more,
  has_newer,
  channels: [],
});

type Query = Readonly<{
  before?: string;
  after?: string;
  ids?: string;
  around?: string;
  limit?: number;
}>;
type Handler = (query: Query) => MessagesPage | Promise<MessagesPage>;

function serve(handler: Handler) {
  get.mockImplementation(async (path: string, options: { params: { query?: Query } }) => {
    if (!path.endsWith("/messages")) throw new Error(`unexpected ${path}`);
    return { data: await handler(options.params.query ?? {}), response: { status: 200 } };
  });
}

function setup(center: string | null = null) {
  const client = new QueryClient();
  const wrapper = ({ children }: Readonly<{ children: ReactNode }>) => (
    <QueryClientProvider client={client}>
      <EventStreamProvider>{children}</EventStreamProvider>
    </QueryClientProvider>
  );
  return renderHook(() => useChannelMessages(CHANNEL, center), { wrapper });
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

  // covers: spec 0012 AC-17
  it("history_changed 攒 5 秒合并成一次，补进来的旧消息插到位置上且不跳动", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let round = 0;
    serve(() => (round++ === 0 ? page(["10"]) : page(["8", "9", "10"])));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));
    const before = get.mock.calls.length;

    emit("history_changed", {});
    emit("history_changed", {});
    emit("history_changed", {});
    await act(() => vi.advanceTimersByTimeAsync(HISTORY_COALESCE_MS - 1));
    expect(get.mock.calls.length).toBe(before);

    await act(() => vi.advanceTimersByTimeAsync(1));

    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10"]));
    expect(get.mock.calls.length).toBe(before + 1);
    expect(result.current.epoch).toBe(0);
  });

  // covers: spec 0012 AC-17
  it("别的频道的 history_changed 不取数", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(() => page(["10"]));
    const { result } = setup();
    await waitFor(() => expect(idsOf(result)).toEqual(["10"]));
    const before = get.mock.calls.length;

    emit("history_changed", {}, "999");
    await act(() => vi.advanceTimersByTimeAsync(HISTORY_COALESCE_MS));

    expect(get.mock.calls.length).toBe(before);
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
  // covers: spec 0013 AC-17
  it("以某条为中心：第一页用 around 取，has_more 管更早、has_newer 管更新", async () => {
    serve((q) => (q.around ? page(["8", "9", "10"], true, true) : page([])));
    const { result } = setup("9");
    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10"]));

    expect(get.mock.calls[0]?.[1].params.query).toEqual({ around: "9" });
    expect(result.current.hasOlder).toBe(true);
    expect(result.current.hasNewer).toBe(true);
    expect(result.current.initialCenter).toBe("9");
  });

  // covers: spec 0013 AC-17, AC-19
  it("向下翻页用 after；某次 has_more 为假就恢复普通行为", async () => {
    serve((q) => {
      if (q.around) return page(["8", "9", "10"], false, true);
      if (q.after === "10") return page(["11", "12"], true);
      if (q.after === "12") return page(["13"], false);
      return page(["13", "14"]);
    });
    const { result } = setup("9");
    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10"]));

    act(() => result.current.loadNewer());
    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10", "11", "12"]));
    expect(result.current.hasNewer).toBe(true);

    act(() => result.current.loadNewer());
    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10", "11", "12", "13"]));
    expect(result.current.hasNewer).toBe(false);

    // 回到普通模式：实时新消息照常追加。
    emit("new_message", { message_id: "14" });
    await waitFor(() => expect(idsOf(result).at(-1)).toBe("14"));
  });

  // covers: spec 0013 AC-19
  it("还没到最新时：实时消息、重连和缺口检查都不取数，已加载消息的编辑照常刷新", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve((q) => {
      if (q.ids) return { ...page([]), messages: [row("9", "edited")] };
      return page(["8", "9", "10"], false, true);
    });
    const { result } = setup("9");
    await waitFor(() => expect(idsOf(result)).toEqual(["8", "9", "10"]));
    const calls = get.mock.calls.length;

    emit("new_message", { message_id: "500" });
    act(() => FakeEventSource.last.onopen?.());
    act(() => FakeEventSource.last.onopen?.());
    await act(() => vi.advanceTimersByTimeAsync(31_000));
    expect(get.mock.calls.length).toBe(calls);

    emit("message_changed", { message_ids: ["9", "500"] });
    await waitFor(() => expect(result.current.messages[1]?.content).toBe("edited"));
    const kinds = get.mock.calls.slice(calls).map((call) => Object.keys(call[1].params.query));
    expect(kinds).toEqual([["ids"]]);
    expect(result.current.epoch).toBe(0);
  });

  // covers: spec 0013 AC-19
  it("补采完成后按当前的中心重新取", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let round = 0;
    serve(() =>
      round++ === 0 ? page(["9", "10"], false, true) : page(["7", "8", "9", "10"], false, true),
    );
    const { result } = setup("9");
    await waitFor(() => expect(idsOf(result)).toEqual(["9", "10"]));

    emit("history_changed", {});
    await act(() => vi.advanceTimersByTimeAsync(HISTORY_COALESCE_MS));

    await waitFor(() => expect(idsOf(result)).toEqual(["7", "8", "9", "10"]));
    expect(get.mock.calls.at(-1)?.[1].params.query).toEqual({ around: "9" });
  });

  // covers: spec 0013 AC-18
  it("中心消息找不到时 centerMissing 为真，状态保持 loading", async () => {
    get.mockResolvedValue({
      error: { error: { code: "message_not_found", message: "x" } },
      response: { status: 404 },
    });
    const { result } = setup("9");

    await waitFor(() => expect(result.current.centerMissing).toBe(true));
    expect(result.current.status).toBe("loading");
  });

  // covers: spec 0013 AC-19
  it("第一次加载时 has_newer 就为假：直接是普通模式", async () => {
    serve((q) => (q.around ? page(["9", "10"], false, false) : page([])));
    const { result } = setup("10");
    await waitFor(() => expect(idsOf(result)).toEqual(["9", "10"]));

    expect(result.current.hasNewer).toBe(false);
  });
});
