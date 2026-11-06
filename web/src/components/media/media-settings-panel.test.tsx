import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import { TooltipProvider } from "@/components/ui/tooltip";

import type { MediaSettings } from "./describe-media";
import { MediaRailLink } from "./media-rail-link";
import { MediaSettingsPanel } from "./media-settings-panel";

const { get, put, post } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, PUT: put, POST: post } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const SETTINGS: MediaSettings = {
  max_file_mb: 50,
  quota_gb: 50,
  used_bytes: 12.5 * 1024 ** 3,
  counts: { pending: 4, stored: 120, failed: 2, too_large: 1, no_space: 0, gone: 3 },
  pending_channel_off: 1,
  storage_full: false,
};

function serve(settings: MediaSettings) {
  get.mockResolvedValue({ data: settings, ...jsonResponse(200) });
}

describe("MediaSettingsPanel", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
    post.mockReset();
  });

  // covers: spec 0011 AC-8
  it("显示上限、已用容量和占比、六个状态的数量", async () => {
    serve(SETTINGS);
    renderWithQuery(<MediaSettingsPanel />);

    expect(await screen.findByText("12.5 GB / 50 GB（25%）")).toBeInTheDocument();
    expect(screen.getByLabelText("单个文件上限（MB）")).toHaveValue("50");
    expect(screen.getByLabelText("总容量上限（GB）")).toHaveValue("50");
    const counts = within(screen.getByText("待保存").closest("dl") as HTMLElement);
    for (const [label, value] of [
      ["待保存", "4"],
      ["已保存", "120"],
      ["失败", "2"],
      ["超过上限", "1"],
      ["空间不足", "0"],
      ["已失效", "3"],
    ]) {
      expect(counts.getByText(label).nextElementSibling).toHaveTextContent(value);
    }
  });

  // covers: spec 0011 AC-23
  it("说明有多少待保存的附件因为频道关闭而暂停", async () => {
    serve(SETTINGS);
    renderWithQuery(<MediaSettingsPanel />);

    expect(await screen.findByText(/1 个所在频道已关闭/)).toBeInTheDocument();
  });

  // covers: spec 0011 AC-8
  it("超出范围时不保存，并在输入框旁说明", async () => {
    serve(SETTINGS);
    renderWithQuery(<MediaSettingsPanel />);
    const input = await screen.findByLabelText("单个文件上限（MB）");

    await userEvent.clear(input);
    await userEvent.type(input, "2000");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("范围是 1 到 1024 MB")).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  // covers: spec 0011 AC-8
  it("合法的值用整数 MB 和 GB 提交", async () => {
    serve(SETTINGS);
    put.mockResolvedValue({ data: { ...SETTINGS, max_file_mb: 100 }, ...jsonResponse(200) });
    renderWithQuery(<MediaSettingsPanel />);
    const input = await screen.findByLabelText("单个文件上限（MB）");

    await userEvent.clear(input);
    await userEvent.type(input, "100");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/media/settings", {
        body: { max_file_mb: 100, quota_gb: 50 },
      }),
    );
  });

  // covers: spec 0011 AC-7
  it("空间已满时区块标红并说明", async () => {
    serve({ ...SETTINGS, storage_full: true });
    renderWithQuery(<MediaSettingsPanel />);

    expect(await screen.findByText(/空间已满，新附件没有保存/)).toBeInTheDocument();
  });

  // covers: spec 0011 AC-17
  it("失败数为 0 时「重试全部失败」禁用，否则可点", async () => {
    serve({ ...SETTINGS, counts: { ...SETTINGS.counts, failed: 0 } });
    const first = renderWithQuery(<MediaSettingsPanel />);
    expect(await screen.findByRole("button", { name: "重试全部失败" })).toBeDisabled();
    first.unmount();

    serve(SETTINGS);
    post.mockResolvedValue({ data: { requeued: 2 }, ...jsonResponse(200) });
    renderWithQuery(<MediaSettingsPanel />);
    const button = await screen.findByRole("button", { name: "重试全部失败" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);

    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/media/retry-failed"));
  });
});

describe("MediaRailLink", () => {
  beforeEach(() => {
    get.mockReset();
  });

  // covers: spec 0011 AC-7
  it("有 no_space 的行时名字是「附件空间已满」", async () => {
    get.mockResolvedValue({
      data: {
        collector: { last_heartbeat_at: null, stale: false, has_account: true },
        backup: { last_run: null, last_success_at: null, overdue: false },
        media: { storage_full: true, last_seen_at: null, stale: false, paused_until: null },
      },
      ...jsonResponse(200),
    });
    renderWithQuery(
      <TooltipProvider>
        <MediaRailLink />
      </TooltipProvider>,
    );

    const link = await screen.findByRole("link", { name: "附件空间已满" });
    expect(link).toHaveAttribute("href", "/settings/media");
  });

  it("没满时只是「附件存储」入口", async () => {
    get.mockResolvedValue({
      data: {
        collector: { last_heartbeat_at: null, stale: false, has_account: true },
        backup: { last_run: null, last_success_at: null, overdue: false },
        media: { storage_full: false, last_seen_at: null, stale: false, paused_until: null },
      },
      ...jsonResponse(200),
    });
    renderWithQuery(
      <TooltipProvider>
        <MediaRailLink />
      </TooltipProvider>,
    );

    expect(await screen.findByRole("link", { name: "附件存储" })).toBeInTheDocument();
  });
});
