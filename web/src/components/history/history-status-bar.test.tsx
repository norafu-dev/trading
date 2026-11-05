import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import { createTimeFormat } from "@/lib/format/time";

import type { HistoryStatus } from "./describe-history";
import { HistoryStatusBar } from "./history-status-bar";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { POST: post, GET: vi.fn() } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const TIME = createTimeFormat("UTC");
const STATUS: HistoryStatus = {
  paused_reason: null,
  paused_at: null,
  waiting_reason: null,
  waiting_until: null,
  pages_used_24h: 120,
  daily_page_limit: 1500,
  quota_resumes_at: null,
  last_seen_at: "2026-11-05T10:00:00Z",
  stale: false,
};

describe("HistoryStatusBar", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: STATUS, ...jsonResponse(200) });
  });

  // covers: spec 0012 AC-8
  it("显示用量，点「暂停全部」发出暂停请求", async () => {
    renderWithQuery(<HistoryStatusBar status={STATUS} time={TIME} />);

    expect(screen.getByText("最近 24 小时已抓 120 / 1500 页")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "暂停全部" }));

    expect(post).toHaveBeenCalledWith("/api/history/control", { body: { paused: true } });
  });

  // covers: spec 0012 AC-7, AC-8
  it("限速自动暂停时说明原因，按钮变成「继续」并发出继续请求", async () => {
    renderWithQuery(
      <HistoryStatusBar
        status={{ ...STATUS, paused_reason: "rate_limited", paused_at: "2026-11-05T09:00:00Z" }}
        time={TIME}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("因限速自动暂停");
    await userEvent.click(screen.getByRole("button", { name: "继续" }));

    expect(post).toHaveBeenCalledWith("/api/history/control", { body: { paused: false } });
  });

  // covers: spec 0012 AC-6
  it("休息中显示继续的时间", () => {
    renderWithQuery(
      <HistoryStatusBar
        status={{ ...STATUS, waiting_reason: "rest", waiting_until: "2026-11-05T10:05:00Z" }}
        time={TIME}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("休息中，");
    expect(screen.getByRole("status")).toHaveTextContent("继续");
  });

  it("有节奏设置的入口", () => {
    renderWithQuery(<HistoryStatusBar status={STATUS} time={TIME} />);

    expect(screen.getByRole("link", { name: "节奏设置" })).toHaveAttribute(
      "href",
      "/settings/history",
    );
  });
});
