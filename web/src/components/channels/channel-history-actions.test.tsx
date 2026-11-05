import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import type { ChannelGapSummary } from "@/components/history/describe-history";

import { ChannelHistoryActions } from "./channel-history-actions";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, POST: post } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// 新建任务对话框另有自己的测试，这里只关心它是不是带着这个频道打开的（AC-18）。
vi.mock("@/components/history/new-job-dialog", () => ({
  NewJobDialog: ({
    open,
    initialChannelIds,
  }: Readonly<{ open: boolean; initialChannelIds: readonly string[] }>) =>
    open ? <div data-testid="new-job-dialog">{initialChannelIds.join(",")}</div> : null,
}));

const SUMMARY: ChannelGapSummary = {
  channel_id: "1",
  guild_name: "公会",
  channel_name: "alerts",
  gaps: [
    { start: "2026-01-01T01:00:00Z", end: "2026-01-01T03:00:00Z" },
    { start: "2026-01-01T05:00:00Z", end: "2026-01-01T05:00:05Z" },
  ],
  total_seconds: 7205,
};

function served(summaries: readonly ChannelGapSummary[]) {
  get.mockResolvedValue({ data: summaries, ...jsonResponse(200) });
  post.mockResolvedValue({ data: { batch_id: "b1", jobs: [], skipped: [] }, ...jsonResponse(200) });
}

const render = () => renderWithQuery(<ChannelHistoryActions channelId="1" channelName="alerts" />);

describe("ChannelHistoryActions", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  // covers: spec 0012 AC-15
  it("有缺口时显示段数和总时长，只查这个频道", async () => {
    served([SUMMARY]);
    render();

    expect(await screen.findByText("这个频道有 2 段缺口，共 2 小时")).toBeInTheDocument();
    expect(get.mock.calls[0]?.[1].params.query).toEqual({ channel_id: "1" });
  });

  // covers: spec 0012 AC-15
  it("没有缺口时不显示提示和「补缺口」", async () => {
    served([]);
    render();

    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByText(/段缺口/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /补缺口/ })).not.toBeInTheDocument();
  });

  // covers: spec 0012 AC-15
  it("点「补缺口」只提交这个频道", async () => {
    served([SUMMARY]);
    render();

    await userEvent.click(await screen.findByRole("button", { name: "补缺口：#alerts" }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0]?.[0]).toBe("/api/history/gaps/fill");
    expect(post.mock.calls[0]?.[1].body).toEqual({ channel_ids: ["1"] });
  });

  // covers: spec 0012 AC-18
  it("「抓这个频道的历史」在没有缺口时也有，打开的对话框预先选好这个频道", async () => {
    served([]);
    render();

    await userEvent.click(screen.getByRole("button", { name: "抓这个频道的历史" }));

    expect(await screen.findByTestId("new-job-dialog")).toHaveTextContent("1");
  });
});
