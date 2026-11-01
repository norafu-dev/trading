import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import type { DiscordAccount } from "@/components/discord/use-discord-account";

import { SystemStatus } from "./system-status";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

const ACCOUNT: DiscordAccount = {
  id: 1,
  label: "小号",
  discord_user_id: "111",
  discord_username: "alt_user",
  status: "online",
  status_changed_at: "2026-11-01T10:00:00Z",
  last_error: null,
  last_heartbeat_at: "2026-11-01T10:00:00Z",
  retry_at: null,
  collector_stale: false,
};

function serve(account: Promise<unknown>) {
  get.mockImplementation((path: string) =>
    path === "/api/health"
      ? Promise.resolve({
          data: { database: "ok", database_time: "2026-11-01T10:00:00Z" },
          ...jsonResponse(200),
        })
      : account,
  );
  renderWithQuery(<SystemStatus />);
}

function serveAccount(account: DiscordAccount | null) {
  serve(Promise.resolve({ data: { account }, ...jsonResponse(200) }));
}

function accountRow() {
  return screen.getByRole("link", { name: /Discord 账号/ });
}

describe("SystemStatus 的 Discord 账号行", () => {
  beforeEach(() => {
    get.mockReset();
  });

  // covers: AC-15
  it("显示账号状态并链接到设置页", async () => {
    serveAccount(ACCOUNT);

    expect(await screen.findByText("在线（隐身）")).toBeInTheDocument();
    expect(accountRow()).toHaveAttribute("href", "/settings/discord");
  });

  // covers: AC-15
  it("collector_stale 时显示采集进程无响应", async () => {
    serveAccount({ ...ACCOUNT, collector_stale: true });

    expect(await screen.findByText("采集进程无响应")).toBeInTheDocument();
    expect(screen.queryByText("在线（隐身）")).not.toBeInTheDocument();
  });

  it("没有账号时显示还没有添加", async () => {
    serveAccount(null);

    expect(await screen.findByText("还没有添加")).toBeInTheDocument();
  });

  it("账号读取中显示检查中", async () => {
    serve(new Promise(() => {}));

    expect(await within(accountRow()).findByText("检查中")).toBeInTheDocument();
  });

  // covers: AC-7
  it("login_paused 显示恢复时间", async () => {
    serveAccount({ ...ACCOUNT, status: "login_paused", retry_at: "2026-11-02T10:00:00Z" });

    expect(await screen.findByText(/已暂停登录，将在 .*2.* 恢复/)).toBeInTheDocument();
  });
});
