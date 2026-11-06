import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/components/auth/test-utils";

import { ChannelPage } from "./channel-page";
import { BANNER_TEXT } from "./channel-status-banner";

const { get, params, replace } = vi.hoisted(() => ({
  get: vi.fn(),
  params: { value: "" },
  replace: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(params.value),
  useRouter: () => ({ replace }),
}));

const EMPTY_PAGE = { messages: [], has_more: false, users: [], channels: [] };

const detail = (banner_reason: string | null) => ({
  id: "1",
  guild_id: "2",
  guild_name: "Signals",
  name: "alerts",
  type: 0,
  banner_reason,
});

function serve(reason: string | null) {
  get.mockImplementation(async (path: string) => ({
    data: path.endsWith("/messages") ? EMPTY_PAGE : detail(reason),
    response: new Response(null, { status: 200 }),
  }));
}

describe("ChannelPage", () => {
  beforeEach(() => {
    get.mockReset();
    replace.mockReset();
    params.value = "id=1";
    vi.stubGlobal("EventSource", undefined);
  });

  // covers: spec 0008 AC-16
  it("没有 id 时提示选频道，不取数", () => {
    params.value = "";
    renderWithQuery(<ChannelPage />);

    expect(screen.getByText("从左边选一个频道")).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });

  // covers: spec 0008 AC-16
  it("频道不存在时显示错误块", async () => {
    get.mockImplementation(async () => ({
      error: { error: { code: "channel_not_found", message: "x" } },
      response: { status: 404 },
    }));
    renderWithQuery(<ChannelPage />);

    expect(await screen.findByText("频道不存在")).toBeInTheDocument();
  });

  // covers: spec 0008 AC-23
  it.each(Object.entries(BANNER_TEXT))("%s 显示对应的提示", async (reason, text) => {
    serve(reason);
    renderWithQuery(<ChannelPage />);

    expect(await screen.findByText(text, { exact: false })).toBeInTheDocument();
  });

  // covers: spec 0008 AC-23
  it("正常采集时没有提示；关闭采集时有去管理页的链接", async () => {
    serve(null);
    const { unmount } = renderWithQuery(<ChannelPage />);
    await waitFor(() => expect(screen.getByText("这个频道还没有采集到消息")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    unmount();

    serve("collect_off");
    renderWithQuery(<ChannelPage />);
    const link = await screen.findByRole("link", { name: "管理采集频道" });
    expect(link).toHaveAttribute("href", "/settings/channels");
  });

  // covers: spec 0013 AC-18
  it("m 指向的消息找不到：提示、从网址去掉 m", async () => {
    params.value = "id=1&m=999";
    get.mockImplementation(async (path: string, options: { params: { query?: object } }) => {
      if (!path.endsWith("/messages")) {
        return { data: detail(null), response: new Response(null, { status: 200 }) };
      }
      return options.params.query && "around" in options.params.query
        ? {
            error: { error: { code: "message_not_found", message: "x" } },
            response: { status: 404 },
          }
        : { data: EMPTY_PAGE, response: new Response(null, { status: 200 }) };
    });
    renderWithQuery(<ChannelPage />);

    expect(await screen.findByText("找不到这条消息，已显示最新消息")).toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/channels?id=1");
  });

  // covers: spec 0013 AC-18
  it("m 不是数字时不请求 around，直接提示并去掉 m", async () => {
    params.value = "id=1&m=abc";
    serve(null);
    renderWithQuery(<ChannelPage />);

    expect(await screen.findByText("找不到这条消息，已显示最新消息")).toBeInTheDocument();
    expect(replace).toHaveBeenCalledWith("/channels?id=1");
    expect(get.mock.calls.some((call) => JSON.stringify(call).includes("around"))).toBe(false);
  });
});
