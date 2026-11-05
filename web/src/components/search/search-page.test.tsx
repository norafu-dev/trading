import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/components/auth/test-utils";

import { SearchPage } from "./search-page";

const { get, params, replace } = vi.hoisted(() => ({
  get: vi.fn(),
  params: { value: "" },
  replace: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(params.value),
  usePathname: () => "/search",
  useRouter: () => ({ replace }),
}));
// jsdom 没有布局，虚拟列表什么都不会渲染，这里让它把所有行都吐出来。
vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 120,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({ index, key: index, start: index * 120 })),
    measureElement: () => undefined,
  }),
}));
vi.mock("@/lib/discord-markdown/use-markdown-parser", () => ({
  useMarkdownParser: () => ({ status: "loading" }),
}));
vi.mock("./search-filters", () => ({
  AuthorPicker: () => null,
  ChannelPicker: () => null,
  ClearFilters: () => null,
  TimePicker: () => null,
}));
vi.mock("./search-hit", () => ({
  SearchHit: ({
    hit,
    highlightTerms,
  }: {
    hit: { message: { id: string } };
    highlightTerms: readonly string[];
  }) => (
    <div data-testid="hit">
      {hit.message.id}|{highlightTerms.join(",")}
    </div>
  ),
}));

const ok = (data: unknown) => ({ data, response: new Response(null, { status: 200 }) });
const fail = (status: number, code: string) => ({
  error: { error: { code, message: "x" } },
  response: { status },
});
const hit = (id: string) => ({
  message: { id },
  guild: { id: "1", name: "g" },
  channel: { id: "2", name: "c", type: 0, status: "active", collect_enabled: true, parent: null },
});
const page = (ids: string[], next: string | null = null) => ({
  items: ids.map(hit),
  next_before: next,
  users: [],
  channels: [],
});

type Routes = Readonly<{
  messages?: () => unknown;
  count?: () => unknown;
  pending?: number;
}>;
function serve(routes: Routes) {
  get.mockImplementation(async (path: string) => {
    if (path === "/api/search/messages") return (routes.messages ?? (() => ok(page([]))))();
    if (path === "/api/search/count")
      return (routes.count ?? (() => ok({ count: 1, capped: false })))();
    if (path === "/api/search/status") return ok({ index_pending: routes.pending ?? 0 });
    return ok({});
  });
}
const searchCalls = () => get.mock.calls.filter(([path]) => path === "/api/search/messages");

describe("SearchPage", () => {
  beforeEach(() => {
    get.mockReset();
    replace.mockReset();
    params.value = "";
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        observe() {}
        disconnect() {}
      },
    );
  });

  // covers: spec 0013 AC-2
  it("没有条件时只显示提示，不发搜索请求", async () => {
    serve({});
    renderWithQuery(<SearchPage />);

    expect(screen.getByText("输入关键词或选择筛选条件")).toBeInTheDocument();
    await waitFor(() => expect(get).toHaveBeenCalledWith("/api/search/status"));
    expect(searchCalls()).toHaveLength(0);
  });

  // covers: spec 0013 AC-4
  it("关键词不合法时在输入框下面提示，不提交", () => {
    serve({});
    renderWithQuery(<SearchPage />);

    fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "a" } });

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "搜索" })).toBeDisabled();
    expect(replace).not.toHaveBeenCalled();
  });

  // covers: spec 0013 AC-1
  it("提交时把关键词写进网址", () => {
    serve({});
    renderWithQuery(<SearchPage />);

    fireEvent.change(screen.getByLabelText("关键词"), { target: { value: "空投 btc" } });
    fireEvent.submit(screen.getByLabelText("关键词").closest("form") as HTMLFormElement);

    expect(replace).toHaveBeenCalledTimes(1);
    expect(String(replace.mock.calls[0]?.[0])).toMatch(/^\/search\?q=/);
  });

  // covers: spec 0013 AC-4
  it("只有排除词又没有筛选时提示，不发请求", async () => {
    params.value = "q=-foo";
    serve({});
    renderWithQuery(<SearchPage />);

    expect(await screen.findByText(/排除/)).toBeInTheDocument();
    expect(searchCalls()).toHaveLength(0);
  });

  // covers: spec 0013 AC-12, AC-13, AC-14
  it("显示结果、总数和高亮词", async () => {
    params.value = "q=btc";
    serve({
      messages: () => ok(page(["11", "10"])),
      count: () => ok({ count: 1000, capped: true }),
    });
    renderWithQuery(<SearchPage />);

    const hits = await screen.findAllByTestId("hit");
    expect(hits.map((h) => h.textContent)).toEqual(["11|btc", "10|btc"]);
    expect(await screen.findByText("1000+ 条")).toBeInTheDocument();
  });

  // covers: spec 0013 AC-14
  it("总数超时（null）时不显示总数，结果照常", async () => {
    params.value = "q=btc";
    serve({ messages: () => ok(page(["11"])), count: () => ok({ count: null, capped: false }) });
    renderWithQuery(<SearchPage />);

    expect(await screen.findByTestId("hit")).toBeInTheDocument();
    expect(screen.queryByText(/ 条$/)).not.toBeInTheDocument();
  });

  // covers: spec 0013 AC-16
  it("没有结果时显示空状态", async () => {
    params.value = "q=btc";
    serve({});
    renderWithQuery(<SearchPage />);

    expect(await screen.findByText("没有找到符合条件的消息")).toBeInTheDocument();
  });

  // covers: spec 0013 AC-16, AC-24
  it("第一页超时显示提示，点重试再取一次", async () => {
    params.value = "q=btc";
    let calls = 0;
    serve({
      messages: () => {
        calls += 1;
        return calls === 1 ? fail(503, "search_timeout") : ok(page(["11"]));
      },
    });
    renderWithQuery(<SearchPage />);

    expect(await screen.findByText(/这次搜索太慢了/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByTestId("hit")).toBeInTheDocument();
  });

  // covers: spec 0013 AC-15
  it("刷新会重新请求第一页", async () => {
    params.value = "q=btc";
    serve({ messages: () => ok(page(["11"])) });
    renderWithQuery(<SearchPage />);
    await screen.findByTestId("hit");
    const before = searchCalls().length;

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    });

    // 相对时间没变时查询键不变，但刷新必须强制重取。
    await waitFor(() => expect(searchCalls().length).toBeGreaterThan(before));
  });

  // covers: spec 0013 AC-23
  it("补建没完成时显示横幅，完成后消失", async () => {
    serve({ pending: 7 });
    renderWithQuery(<SearchPage />);

    expect(
      await screen.findByText("搜索索引补建中，结果可能不全（还剩 7 条）"),
    ).toBeInTheDocument();
  });

  // covers: spec 0013 AC-23
  it("补建完成（0 条）时没有横幅", async () => {
    serve({ pending: 0 });
    renderWithQuery(<SearchPage />);

    await waitFor(() => expect(get).toHaveBeenCalledWith("/api/search/status"));
    expect(screen.queryByText(/补建中/)).not.toBeInTheDocument();
  });

  // covers: spec 0013 AC-12, AC-16
  it("滚到底自动加载下一页，下一页失败时保留已有结果并可重试", async () => {
    vi.stubGlobal(
      "IntersectionObserver",
      class {
        constructor(private readonly callback: (entries: { isIntersecting: boolean }[]) => void) {}
        observe() {
          this.callback([{ isIntersecting: true }]);
        }
        disconnect() {}
      },
    );
    params.value = "q=btc";
    const second = vi
      .fn()
      .mockReturnValueOnce(fail(500, "boom"))
      .mockReturnValue(ok(page(["9"])));
    serve({
      messages: () => {
        const before = searchCalls().at(-1)?.[1]?.params?.query?.before;
        return before ? second() : ok(page(["11", "10"], "10"));
      },
    });
    renderWithQuery(<SearchPage />);

    expect(await screen.findByText("服务器出错了")).toBeInTheDocument();
    expect(screen.getAllByTestId("hit")).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(screen.getAllByTestId("hit")).toHaveLength(3));
  });
});
