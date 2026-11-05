import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import type { HistorySettings } from "./describe-history";
import { HistorySettingsPanel } from "./history-settings-panel";

const { get, put } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, PUT: put } }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const SETTINGS: HistorySettings = {
  page_delay_min_s: 2,
  page_delay_max_s: 4,
  daily_page_limit: 1500,
  run_minutes: 20,
  rest_minutes: 5,
  auto_gap_max_hours: 6,
  pages_used_24h: 321,
};

describe("HistorySettingsPanel", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
    get.mockResolvedValue({ data: SETTINGS, ...jsonResponse(200) });
  });

  // covers: spec 0012 AC-19
  it("显示六个数值和当前滚动 24 小时的用量", async () => {
    renderWithQuery(<HistorySettingsPanel />);

    expect(await screen.findByText("已抓 321 / 1500 页")).toBeInTheDocument();
    expect(screen.getByLabelText("每页最短间隔（秒）")).toHaveValue("2");
    expect(screen.getByLabelText("每页最长间隔（秒）")).toHaveValue("4");
    expect(screen.getByLabelText("每 24 小时页数上限")).toHaveValue("1500");
    expect(screen.getByLabelText("连续抓取多久后休息（分钟）")).toHaveValue("20");
    expect(screen.getByLabelText("每次休息多久（分钟）")).toHaveValue("5");
    expect(screen.getByLabelText(/自动补缺口的时长上限/)).toHaveValue("6");
  });

  // covers: spec 0012 AC-19
  it("超出范围时不保存并显示错误", async () => {
    renderWithQuery(<HistorySettingsPanel />);
    const limit = await screen.findByLabelText("每 24 小时页数上限");

    await userEvent.clear(limit);
    await userEvent.type(limit, "10");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请输入 50 到 20000 之间的整数");
    expect(put).not.toHaveBeenCalled();
  });

  // covers: spec 0012 AC-19
  it("最短间隔大于最长间隔时不保存", async () => {
    renderWithQuery(<HistorySettingsPanel />);
    const min = await screen.findByLabelText("每页最短间隔（秒）");

    await userEvent.clear(min);
    await userEvent.type(min, "9");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("最短间隔不能大于最长间隔");
    expect(put).not.toHaveBeenCalled();
  });

  // covers: spec 0012 AC-19
  it("合法的值整体保存", async () => {
    put.mockResolvedValue({ data: { ...SETTINGS, run_minutes: 30 }, ...jsonResponse(200) });
    renderWithQuery(<HistorySettingsPanel />);
    const run = await screen.findByLabelText("连续抓取多久后休息（分钟）");

    await userEvent.clear(run);
    await userEvent.type(run, "30");
    await userEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/history/settings", {
        body: {
          page_delay_min_s: 2,
          page_delay_max_s: 4,
          daily_page_limit: 1500,
          run_minutes: 30,
          rest_minutes: 5,
          auto_gap_max_hours: 6,
        },
      }),
    );
  });
});
