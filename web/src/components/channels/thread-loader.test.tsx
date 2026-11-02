import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/components/auth/test-utils";

import { ThreadLoader, threadLoaderState } from "./thread-loader";
import type { GuildSummary } from "./use-guilds";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { POST: post } }));

const GUILD: GuildSummary = {
  id: "1",
  name: "Signals",
  icon_hash: null,
  status: "active",
  enabled_count: 0,
  thread_load_pending: false,
};

describe("threadLoaderState", () => {
  // covers: spec 0008 AC-12
  it("没有请求时可以点；排队时按账号状态说明", () => {
    expect(threadLoaderState(false, true)).toEqual({ label: "加载线程", disabled: false });
    expect(threadLoaderState(true, true)).toEqual({ label: "正在加载", disabled: true });
    expect(threadLoaderState(true, false)).toEqual({ label: "账号上线后加载", disabled: true });
  });
});

describe("ThreadLoader", () => {
  beforeEach(() => post.mockReset());

  // covers: spec 0008 AC-12
  it("点击发请求；没有线程时提示可能不全", async () => {
    post.mockResolvedValue({ data: { pending: true }, response: { status: 202 } });
    renderWithQuery(<ThreadLoader guild={GUILD} online channels={[{ type: 0 }]} />);

    expect(screen.getByText("这个服务器还没订阅，线程可能不全")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "加载线程" }));

    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    expect(post.mock.calls[0]?.[1].params.path.guild_id).toBe("1");
  });

  // covers: spec 0008 AC-12
  it("已有线程时不提示；请求没处理时按钮禁用", () => {
    renderWithQuery(
      <ThreadLoader
        guild={{ ...GUILD, thread_load_pending: true }}
        online
        channels={[{ type: 11 }]}
      />,
    );

    expect(screen.queryByText("这个服务器还没订阅，线程可能不全")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在加载" })).toBeDisabled();
  });
});
