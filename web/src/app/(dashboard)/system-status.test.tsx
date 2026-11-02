import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import type { DiscordAccount } from "@/components/discord/use-discord-account";

import { SYSTEM_STATUS, systemStatusResponse } from "@/components/system/test-fixtures";
import type { SystemStatusData } from "@/components/system/use-system-status";

import { SystemStatus } from "./system-status";

const { get, systemGet } = vi.hoisted(() => ({ get: vi.fn(), systemGet: vi.fn() }));
// 系统状态单独分流，账号和健康检查的用例不用管它。
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (path: string, ...rest: unknown[]) =>
      path === "/api/system/status" ? systemGet() : get(path, ...rest),
  },
}));

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
    systemGet.mockReset();
    systemGet.mockResolvedValue(systemStatusResponse());
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

function serveSystem(overrides: Partial<SystemStatusData>) {
  systemGet.mockResolvedValue(systemStatusResponse({ ...SYSTEM_STATUS, ...overrides }));
  serveAccount(ACCOUNT);
}

function row(label: string) {
  const term = screen.getByText(label, { selector: "dt" });
  const container = term.parentElement;
  if (!container) throw new Error("no row");
  return within(container);
}

const SUCCEEDED = {
  started_at: "2026-11-01T04:00:00Z",
  finished_at: "2026-11-01T04:03:12Z",
  status: "succeeded",
  size_bytes: 12_345_678,
  object_key: "harvester-20261101T040000Z.dump.age",
  error: null,
} as const;

describe("SystemStatus 的采集进程和备份行（spec 0009）", () => {
  beforeEach(() => {
    get.mockReset();
    systemGet.mockReset();
  });

  // covers: AC-4
  it("心跳新鲜时显示正常和相对时间", async () => {
    serveSystem({});

    expect(await row("采集进程").findByText("正常")).toBeInTheDocument();
    expect(row("采集进程").getByText(/^最近心跳 /)).toHaveAttribute("title");
  });

  // covers: AC-4
  it("stale 时显示采集进程无响应", async () => {
    serveSystem({
      collector: { last_heartbeat_at: "2026-11-01T10:00:00Z", stale: true, has_account: true },
    });

    expect(await row("采集进程").findByText("采集进程无响应")).toBeInTheDocument();
  });

  // covers: AC-4
  it("没有账号也没有心跳时显示未运行", async () => {
    serveSystem({ collector: { last_heartbeat_at: null, stale: false, has_account: false } });

    expect(await row("采集进程").findByText("未运行")).toBeInTheDocument();
  });

  // covers: AC-7
  it("还没有备份时不告警", async () => {
    serveSystem({});

    expect(await row("备份").findByText("还没有备份")).toBeInTheDocument();
  });

  // covers: AC-7
  it("成功时显示大小（MB，一位小数）", async () => {
    serveSystem({
      backup: { last_run: SUCCEEDED, last_success_at: SUCCEEDED.finished_at, overdue: false },
    });

    expect(await row("备份").findByText("成功")).toBeInTheDocument();
    expect(row("备份").getByText(/12\.3 MB$/)).toBeInTheDocument();
  });

  // covers: AC-7
  it.each([
    ["dump", "失败：导出"],
    ["encrypt", "失败：加密"],
    ["upload", "失败：上传"],
    ["interrupted", "失败：被中断"],
  ] as const)("失败步骤 %s 显示为中文", async (error, text) => {
    serveSystem({
      backup: {
        last_run: {
          ...SUCCEEDED,
          status: "failed",
          size_bytes: null,
          object_key: null,
          error,
        },
        last_success_at: null,
        overdue: true,
      },
    });

    expect(await row("备份").findByText(text)).toBeInTheDocument();
  });

  // covers: AC-7
  it("进行中显示备份中", async () => {
    serveSystem({
      backup: {
        last_run: {
          ...SUCCEEDED,
          status: "running",
          finished_at: null,
          size_bytes: null,
          object_key: null,
        },
        last_success_at: null,
        overdue: true,
      },
    });

    expect(await row("备份").findByText("备份中")).toBeInTheDocument();
    expect(row("备份").getByText(/^开始于 /)).toBeInTheDocument();
  });
});
