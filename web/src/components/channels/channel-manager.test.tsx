import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import { ChannelManager } from "./channel-manager";

const { get, put, replace, params, toastError, toastSuccess } = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  replace: vi.fn(),
  params: { value: "" },
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
}));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, PUT: put } }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => new URLSearchParams(params.value),
}));
vi.mock("sonner", () => ({ toast: { error: toastError, success: toastSuccess } }));

const ACCOUNT = { id: 1, label: "小号", status: "online" };
const GUILDS: Readonly<{ synced_at: string | null; guilds: readonly unknown[] }> = {
  synced_at: "2026-11-01T10:00:00Z",
  guilds: [{ id: "1", name: "Signals", icon_hash: null, status: "active", enabled_count: 0 }],
};
const BASE = {
  guild_id: "1",
  parent_id: null,
  position: 0,
  status: "active",
  collect_enabled: false,
  collectable: true,
  can_read: true,
  type: 0,
};
const CHANNELS = [
  { ...BASE, id: "10", name: "alerts" },
  { ...BASE, id: "11", name: "secret", can_read: false },
  { ...BASE, id: "12", name: "gone", can_read: false, collect_enabled: true },
];

function serve({
  account = ACCOUNT as unknown,
  guilds = GUILDS,
  channels = CHANNELS as readonly unknown[],
} = {}) {
  get.mockImplementation(async (path: string) => {
    if (path === "/api/discord/account") return { data: { account }, ...jsonResponse(200) };
    if (path === "/api/discord/guilds") return { data: guilds, ...jsonResponse(200) };
    return { data: { channels }, ...jsonResponse(200) };
  });
}

// 全量并行跑时首次渲染较慢，默认的 1 秒不够。
function findSwitch(name: string) {
  return screen.findByRole("switch", { name }, { timeout: 4_000 });
}

describe("ChannelManager", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
    replace.mockReset();
    toastError.mockReset();
    toastSuccess.mockReset();
    params.value = "";
  });

  // covers: AC-5, AC-11, AC-16
  it("隐藏无权限频道并显示计数，失去权限的始终显示且可关", async () => {
    serve();
    renderWithQuery(<ChannelManager />);

    const toggle = await findSwitch("采集 #alerts");
    expect(toggle).not.toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    expect(screen.getByText("已隐藏 1 个无权限频道")).toBeInTheDocument();
    expect(screen.getByText("已失去权限，采集已暂停")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "采集 #gone" })).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText(/^上次同步于/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("switch", { name: "显示无权限频道" }));

    expect(screen.getByText("无权限")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "采集 #secret" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  // covers: AC-12
  it("开关乐观更新，失败时回滚并提示", async () => {
    serve();
    let fail: (value: unknown) => void = () => {};
    put.mockReturnValue(
      new Promise((resolve) => {
        fail = resolve;
      }),
    );
    renderWithQuery(<ChannelManager />);
    const toggle = await findSwitch("采集 #alerts");

    fireEvent.click(toggle);

    await waitFor(() => expect(toggle).toBeChecked());
    fail({ error: { error: { code: "channel_no_access" } }, ...jsonResponse(409) });
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  // covers: AC-14
  it("账号离线时提示并照常显示", async () => {
    serve({ account: { ...ACCOUNT, status: "reconnecting" } });
    renderWithQuery(<ChannelManager />);

    expect(await screen.findByText("账号离线时开关会先保存，上线后生效。")).toBeInTheDocument();
    expect(await findSwitch("采集 #alerts")).not.toHaveAttribute("aria-disabled", "true");
  });

  // covers: AC-14
  it("没有账号时引导去账号页", async () => {
    serve({ account: null });
    renderWithQuery(<ChannelManager />);

    const link = await screen.findByRole("link", { name: "去添加账号" });
    expect(link).toHaveAttribute("href", "/settings/discord");
  });

  // covers: AC-11, AC-14
  it("还没同步过时不按权限隐藏或禁用", async () => {
    serve({ guilds: { ...GUILDS, synced_at: null } });
    renderWithQuery(<ChannelManager />);

    expect(await findSwitch("采集 #secret")).not.toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("还没有同步过")).toBeInTheDocument();
    expect(screen.queryByText("已失去权限，采集已暂停")).not.toBeInTheDocument();
  });

  it("没有服务器且没同步过时显示提示", async () => {
    serve({ guilds: { synced_at: null, guilds: [] } });
    renderWithQuery(<ChannelManager />);

    expect(await screen.findByText("账号上线后会自动同步。")).toBeInTheDocument();
  });

  // covers: AC-10
  it("搜索无结果时显示空状态", async () => {
    serve();
    renderWithQuery(<ChannelManager />);
    await findSwitch("采集 #alerts");

    fireEvent.change(screen.getByRole("searchbox", { name: "搜索频道" }), {
      target: { value: "zzz" },
    });

    expect(screen.getByText("没有匹配的频道")).toBeInTheDocument();
  });

  // covers: AC-9
  it("分类批量用 toast 说明结果", async () => {
    const category = { ...BASE, id: "9", name: "交易", type: 4, collectable: false };
    serve({ channels: [category, { ...BASE, id: "10", name: "alerts", parent_id: "9" }] });
    put.mockResolvedValue({ data: { changed: 2, skipped_no_access: 1 }, ...jsonResponse(200) });
    renderWithQuery(<ChannelManager />);

    fireEvent.click(await screen.findByRole("button", { name: "全部开启 交易" }));

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("已开启 2 个频道，跳过 1 个无权限频道"),
    );
  });

  // covers: AC-4, AC-16
  it("选服务器写进网址，选中后有返回按钮", async () => {
    params.value = "guild=1";
    serve();
    renderWithQuery(<ChannelManager />);
    await findSwitch("采集 #alerts");

    fireEvent.click(screen.getByRole("button", { name: /Signals/ }));
    expect(replace).toHaveBeenCalledWith("/settings/channels?guild=1", { scroll: false });

    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(replace).toHaveBeenCalledWith("/settings/channels", { scroll: false });
  });
});
