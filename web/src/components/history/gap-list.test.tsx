import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import { createTimeFormat } from "@/lib/format/time";

import type { ChannelGapSummary } from "./describe-history";
import { GapList } from "./gap-list";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, POST: post } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("next/link", () => ({
  default: ({ href, children }: Readonly<{ href: string; children: React.ReactNode }>) => (
    <a href={href}>{children}</a>
  ),
}));

const TIME = createTimeFormat("UTC");

const gap = (channelId: string, name: string, seconds: number): ChannelGapSummary => ({
  channel_id: channelId,
  guild_name: "公会",
  channel_name: name,
  gaps: [
    { start: "2026-01-01T01:00:00Z", end: "2026-01-01T03:00:00Z" },
    { start: "2026-01-01T05:00:00Z", end: "2026-01-01T05:00:05Z" },
  ],
  total_seconds: seconds,
});

const CHANNELS = [gap("1", "general", 7205), gap("2", "random", 65)];

function served(channels: readonly ChannelGapSummary[]) {
  get.mockResolvedValue({ data: channels, ...jsonResponse(200) });
  post.mockResolvedValue({ data: { batch_id: "b1", jobs: [], skipped: [] }, ...jsonResponse(200) });
}

describe("GapList", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  // covers: spec 0012 AC-15
  it("每个有缺口的频道一行，显示段数、总时长和最早的缺口时间", async () => {
    served(CHANNELS);
    renderWithQuery(<GapList time={TIME} />);

    expect(await screen.findByText("#general")).toBeInTheDocument();
    expect(screen.getByText(/2 段 · 共 2 小时 · 最早 /)).toBeInTheDocument();
    expect(screen.getByText(/2 段 · 共 1 分 5 秒 · 最早 /)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /general/ })).toHaveAttribute(
      "href",
      expect.stringContaining("1"),
    );
  });

  // covers: spec 0012 AC-15
  it("没有缺口时不显示列表和「全部补上」", async () => {
    served([]);
    renderWithQuery(<GapList time={TIME} />);

    expect(await screen.findByText("没有缺口，采集到的时间是连续的")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全部补上" })).not.toBeInTheDocument();
  });

  // covers: spec 0012 AC-15
  it("逐个补只提交这一个频道", async () => {
    served(CHANNELS);
    renderWithQuery(<GapList time={TIME} />);

    await userEvent.click(await screen.findByRole("button", { name: "补缺口：#random" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toBe("/api/history/gaps/fill");
    expect(post.mock.calls[0]?.[1].body).toEqual({ channel_ids: ["2"] });
  });

  // covers: spec 0012 AC-15
  it("「全部补上」一次提交所有有缺口的频道", async () => {
    served(CHANNELS);
    renderWithQuery(<GapList time={TIME} />);

    await userEvent.click(await screen.findByRole("button", { name: "全部补上" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[1].body).toEqual({ channel_ids: ["1", "2"] });
  });

  // covers: spec 0012 AC-15
  it("取缺口失败时显示错误块，可以重试", async () => {
    get.mockResolvedValue({ error: {}, ...jsonResponse(500) });
    renderWithQuery(<GapList time={TIME} />);

    expect(await screen.findByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "全部补上" })).not.toBeInTheDocument();
  });
});
