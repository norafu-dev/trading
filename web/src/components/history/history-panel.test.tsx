import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import type { ChannelGapSummary, HistoryJob, HistoryJobPage } from "./describe-history";
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
      channel_id: "202",
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

const NO_JOBS: HistoryJobPage = { jobs: [], batches: [], next_before_id: null };

function serve(
  page: HistoryJobPage,
  channels: typeof CHANNELS = CHANNELS,
  finished: readonly HistoryJobPage[] = [NO_JOBS],
  gaps: readonly ChannelGapSummary[] = [],
) {
  const finishedCalls: Array<Record<string, unknown>> = [];
  get.mockImplementation(
    (path: string, options?: { params?: { query?: Record<string, unknown> } }) => {
      const query = options?.params?.query ?? {};
      if (path === "/api/history/jobs" && query.view === "finished") {
        finishedCalls.push(query);
        const data = finished[Math.min(finishedCalls.length - 1, finished.length - 1)];
        return Promise.resolve({ data, ...jsonResponse(200) });
      }
      if (path === "/api/history/jobs")
        return Promise.resolve({ data: page, ...jsonResponse(200) });
      if (path === "/api/history/gaps")
        return Promise.resolve({ data: gaps, ...jsonResponse(200) });
      if (path === "/api/discord/guilds")
        return Promise.resolve({ data: GUILDS, ...jsonResponse(200) });
      return Promise.resolve({ data: channels, ...jsonResponse(200) });
    },
  );
  return finishedCalls;
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
    await user.click(within(dialog).getByRole("button", { name: /其他频道（1）/ }));
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

  // covers: spec 0012 AC-1
  it("开了实时采集的频道在前，其他频道默认收起，搜索时一起搜", async () => {
    serve({ jobs: [], batches: [], next_before_id: null });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await user.click(
      (await screen.findAllByRole("button", { name: "新建任务" }))[0] as HTMLElement,
    );
    const dialog = await screen.findByRole("dialog");
    const collecting = await within(dialog).findByRole("region", { name: "正在采集" });
    expect(within(collecting).getByText("alerts")).toBeInTheDocument();
    const toggle = within(dialog).getByRole("button", { name: /其他频道（1）/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(dialog).queryByText("quiet")).not.toBeInTheDocument();

    await user.type(within(dialog).getByRole("textbox", { name: "搜索频道" }), "qui");

    const others = within(dialog).getByRole("region", { name: "其他频道" });
    expect(within(others).getByText("quiet")).toBeInTheDocument();
    expect(within(dialog).queryByRole("region", { name: "正在采集" })).not.toBeInTheDocument();
  });

  it("没有开实时采集的频道时，其他频道一开始就展开", async () => {
    serve(
      { jobs: [], batches: [], next_before_id: null },
      { channels: CHANNELS.channels.map((c) => ({ ...c, collect_enabled: false })) },
    );
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await user.click(
      (await screen.findAllByRole("button", { name: "新建任务" }))[0] as HTMLElement,
    );
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByRole("button", { name: /其他频道（2）/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(dialog).getByText("quiet")).toBeInTheDocument();
  });

  // covers: spec 0012 AC-9
  it("每个任务按状态显示能点的操作，点击后调用接口", async () => {
    serve(PAGE);
    post.mockResolvedValue({ data: JOB, ...jsonResponse(200) });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await screen.findByRole("progressbar", { name: "#alerts 进度" });
    expect(screen.getByRole("button", { name: "暂停：#alerts" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试：#news" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续：#alerts" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重试：#news" }));

    expect(post).toHaveBeenCalledWith("/api/history/jobs/{job_id}/{action}", {
      params: { path: { job_id: "2", action: "retry" } },
    });
  });

  // covers: spec 0012 AC-9
  it("整批操作只列有符合行的，点击后调用批次接口", async () => {
    serve(PAGE);
    post.mockResolvedValue({ data: { changed: 1 }, ...jsonResponse(200) });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    await screen.findByRole("progressbar", { name: "#alerts 进度" });
    expect(screen.getByRole("button", { name: "整批暂停" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "整批继续" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "整批取消" }));

    expect(post).toHaveBeenCalledWith("/api/history/batches/{batch_id}/{action}", {
      params: { path: { batch_id: "b1", action: "cancel" } },
    });
  });

  // covers: spec 0012 AC-14
  it("已结束列表默认最近 7 天，分页加载，可以切到全部", async () => {
    const done = (id: string, name: string): HistoryJob => ({
      ...JOB,
      id,
      batch_id: `f${id}`,
      channel_name: name,
      status: "done",
      progress: 1,
      wait_reason: null,
      wait_until: null,
      finished_at: "2026-01-04T00:00:00Z",
    });
    const batch = (id: string) => ({
      batch_id: `f${id}`,
      channel_count: 1,
      fetched_count: 5,
      status_counts: { pending: 0, running: 0, paused: 0, done: 1, failed: 0, cancelled: 0 },
    });
    const calls = serve(NO_JOBS, CHANNELS, [
      { jobs: [done("10", "first")], batches: [batch("10")], next_before_id: "10" },
      { jobs: [done("9", "second")], batches: [batch("9")], next_before_id: null },
      NO_JOBS,
    ]);
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    expect(await screen.findByRole("progressbar", { name: "#first 进度" })).toBeInTheDocument();
    expect(calls[0]).toMatchObject({ view: "finished", since_days: 7 });
    expect(screen.queryByRole("button", { name: /暂停|取消|重试/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "加载更多" }));

    expect(await screen.findByRole("progressbar", { name: "#second 进度" })).toBeInTheDocument();
    expect(calls[1]).toMatchObject({ before_id: "10" });
    expect(screen.queryByRole("button", { name: "加载更多" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全部（保留 30 天）" }));

    expect(await screen.findByText("全部（保留 30 天）内没有结束的任务")).toBeInTheDocument();
    expect(calls.at(-1)).toMatchObject({ since_days: 30 });
  });

  // covers: spec 0012 AC-14
  it("同一个频道的多段任务合成一个总进度，子任务折叠，有失败时展开", async () => {
    const piece = (id: string, patch: Partial<HistoryJob>): HistoryJob => ({
      ...JOB,
      id,
      wait_reason: null,
      wait_until: null,
      ...patch,
    });
    const jobs = [
      piece("1", {
        range_start: "2026-01-01T00:00:00Z",
        range_end: "2026-01-02T00:00:00Z",
        progress: 0.5,
      }),
      piece("2", {
        range_start: "2026-01-02T00:00:00Z",
        range_end: "2026-01-02T00:00:10Z",
        status: "pending",
        progress: 0,
        fetched_count: 0,
        cursor_at: null,
      }),
    ];
    serve({
      jobs,
      batches: [
        {
          batch_id: "b1",
          channel_count: 1,
          fetched_count: 240,
          status_counts: { pending: 1, running: 1, paused: 0, done: 0, failed: 0, cancelled: 0 },
        },
      ],
      next_before_id: null,
    });
    post.mockResolvedValue({ data: { changed: 2 }, ...jsonResponse(200) });
    const user = userEvent.setup();
    renderWithQuery(<HistoryPanel />);

    expect(await screen.findAllByRole("progressbar", { name: "#alerts 进度" })).toHaveLength(1);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    expect(screen.getByText("共 2 段，展开")).toBeInTheDocument();
    expect(screen.queryByText("排队中", { selector: "span" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /共 2 段/ }));
    expect(screen.getAllByText("排队中").length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole("button", { name: "暂停：#alerts" })[0] as HTMLElement);
    expect(post).toHaveBeenCalledWith(
      "/api/history/batches/{batch_id}/channels/{channel_id}/{action}",
      { params: { path: { batch_id: "b1", channel_id: "201", action: "pause" } } },
    );
  });
});
