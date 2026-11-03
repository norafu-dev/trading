import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";
import { TooltipProvider } from "@/components/ui/tooltip";

import { AccountRailLink } from "./account-rail-link";
import type { DiscordAccount } from "./use-discord-account";

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

function renderRail() {
  renderWithQuery(
    <TooltipProvider>
      <AccountRailLink />
    </TooltipProvider>,
  );
}

function showAccount(account: DiscordAccount | null) {
  get.mockResolvedValue({ data: { account }, ...jsonResponse(200) });
  renderRail();
}

describe("AccountRailLink", () => {
  beforeEach(() => {
    get.mockReset();
  });

  // covers: AC-15
  it("读屏文字带上账号状态，链接指向设置页", async () => {
    showAccount(ACCOUNT);

    const link = await screen.findByRole("link", { name: "Discord 账号：在线（隐身）" });

    expect(link).toHaveAttribute("href", "/settings/discord");
  });

  // covers: AC-8
  it("需要处理的状态直接写在链接名字里", async () => {
    showAccount({ ...ACCOUNT, status: "token_invalid" });

    expect(
      await screen.findByRole("link", {
        name: "Discord 账号：token 失效或账号被封，请到 Discord 确认",
      }),
    ).toBeInTheDocument();
  });

  // covers: AC-15
  it("collector_stale 优先于账号状态", async () => {
    showAccount({ ...ACCOUNT, collector_stale: true });

    expect(await screen.findByRole("link", { name: "采集进程无响应" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /在线/ })).not.toBeInTheDocument();
  });

  it("没有账号时引导添加", async () => {
    showAccount(null);

    expect(await screen.findByRole("link", { name: "添加 Discord 账号" })).toBeInTheDocument();
  });

  it("还没读到账号时仍然能进设置页", () => {
    get.mockReturnValue(new Promise(() => {}));
    renderRail();

    expect(screen.getByRole("link", { name: "Discord 账号" })).toHaveAttribute(
      "href",
      "/settings/discord",
    );
  });
});
