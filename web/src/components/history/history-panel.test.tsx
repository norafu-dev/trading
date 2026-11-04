import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import type { HistoryJob, HistoryJobPage } from "./describe-history";
import { HistoryPanel } from "./history-panel";

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, POST: post } }));

const JOB: HistoryJob = {
  id: "1",
  batch_id: "b1",
  channel_id: "201",
  guild_name: "Signals",
  channel_name: "alerts",
  origin: "manual",
  refetch: true,
  range_start: "2026-01-01T00:00:00Z",
  range_end: "2026-01-02T00:00:00Z",
  status: "running",
  fetched_count: 240,
  pages_fetched: 3,
  cursor_at: "2026-01-01T12:00:00Z",
  progress: 0.5,
  wait_reason: "rate_limited",
  wait_until: "2026-01-03T00:01:00Z",
  not_before: null,
  error: null,
  created_at: "2026-01-03T00:00:00Z",
  started_at: "2026-01-03T00:00:00Z",
  finished_at: null,
};

const PAGE: HistoryJobPage = {
  jobs: [
    JOB,
    {
      ...JOB,
      id: "2",
      channel_name: "news",
      status: "failed",
      error: "suspect_empty",
      wait_reason: null,
      wait_until: null,
    },
  ],
  batches: [
    {
      batch_id: "b1",
      channel_count: 2,
      fetched_count: 480,
      status_counts: { pending: 0, running: 1, paused: 0, done: 0, failed: 1, cancelled: 0 },
    },
  ],
  next_before_id: null,
};

const GUILDS = {
  synced_at: null,
  guilds: [
    {
      id: "100",
      name: "Signals",
      icon_hash: null,
      status: "active",
      enabled_count: 1,
      thread_load_pending: false,
    },
  ],
};

const CHANNELS = {
  channels: [
    {
      id: "201",
      guild_id: "100",
      parent_id: null,
      type: 0,
      name: "alerts",
      position: 0,
      status: "active",
      collect_enabled: true,
      collectable: true,
      can_read: true,
    },
    {
      id: "202",
      guild_id: "100",
      parent_id: null,
      type: 0,
      name: "secret",
      position: 1,
      status: "active",
      collect_enabled: false,
      collectable: true,
      can_read: false,
    },
    {
      id: "203",
      guild_id: "100",
      parent_id: null,
      type: 0,
      name: "quiet",
      position: 2,
      status: "active",
      collect_enabled: false,
      collectable: true,
      can_read: true,
    },
  ],
};

function serve(page: HistoryJobPage) {
  get.mockImplementation((path: string) => {
    if (path === "/api/history/jobs") return Promise.resolve({ data: page, ...jsonResponse(200) });
    if (path === "/api/discord/guilds")
      return Promise.resolve({ data: GUILDS, ...jsonResponse(200) });
    return Promise.resolve({ data: CHANNELS, ...jsonResponse(200) });
  });
}

describe("HistoryPanel", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  // covers: spec 0012 AC-14
  it("按批次显示任务的进度、条数、等待原因和失败原因", async () => {
    serve(PAGE);
    renderWithQuery(<HistoryPanel />);

    const bar = await screen.findByRole("progressbar", { name: "#alerts 进度" });
    expect(bar).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText("2 个频道 · 共 480 条 · 抓取中 1 · 失败 1")).toBeInTheDocument();
    expect(screen.getAllByText("已抓 240 条")).toHaveLength(2);
    expect(screen.getByText(/被限速，等待中/)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("可能缺少「读取消息历史」权限");
    expect(screen.getAllByText("手动 · 整段重抓")).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: /alerts/ })[0]).toHaveAttribute(
      "href",
      "/channels?id=201",
    );
  });

  it("没有进行中的任务时显示空状态", async () => {
    serve({ jobs: [], batches: [], next_before_id: null });
    renderWithQuery(<HistoryPanel />);
    expect(await screen.findByText("没有进行中的任务")).toBeInTheDocument();
  });

  // covers: spec 0012 AC-1, AC-2
  it("新建任务只列小号能读的频道，提交后显示跳过的原因", async () => {
    serve({ jobs: [], batches: [], next_before_id: null });
    post.mockResolvedValue({
      data: {
        batch_id: "b9",
        jobs: [JOB],
        skipped: [{ channel_id: "203", reason: "nothing_to_fetch" }],
      },
      ...jsonResponse(200),
    });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await user.click(
      (await screen.findAllByRole("button", { name: "新建任务" }))[0] as HTMLElement,
    );
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("alerts");
    expect(within(dialog).queryByText("secret")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("checkbox", { name: /alerts/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /quiet/ }));
    await user.click(within(dialog).getByRole("button", { name: "全部历史" }));
    await user.click(within(dialog).getByRole("button", { name: "建任务" }));

    expect(post).toHaveBeenCalledWith("/api/history/jobs", {
      body: { channel_ids: ["201", "203"], range_start: null, range_end: null, refetch: false },
    });
    expect(await within(dialog).findByText("已为 1 个频道建了 1 个任务")).toBeInTheDocument();
    expect(within(dialog).getByText("这段时间已经采齐了")).toBeInTheDocument();
  });

  it("没选频道时不提交", async () => {
    serve({ jobs: [], batches: [], next_before_id: null });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await user.click(
      (await screen.findAllByRole("button", { name: "新建任务" }))[0] as HTMLElement,
    );
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("alerts");
    await user.click(within(dialog).getByRole("button", { name: "建任务" }));

    expect(within(dialog).getByRole("alert")).toHaveTextContent("至少选一个频道");
    expect(post).not.toHaveBeenCalled();
  });
});
