import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import { AccountPanel } from "./account-panel";
import type { DiscordAccount } from "./use-discord-account";

const { get, post, put } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get, POST: post, PUT: put } }));

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

function showAccount(overrides: Partial<DiscordAccount>) {
  get.mockResolvedValue({
    data: { account: { ...ACCOUNT, ...overrides } },
    ...jsonResponse(200),
  });
  renderWithQuery(<AccountPanel />);
}

const CASES: ReadonlyArray<
  readonly [Partial<DiscordAccount>, string, readonly string[], readonly string[]]
> = [
  [{ status: "connecting" }, "正在连接", ["停用"], ["更换 token"]],
  [{ status: "online" }, "在线（隐身）", ["更换 token", "停用"], ["重新连接"]],
  [{ status: "reconnecting" }, "连接中断，正在重试", ["更换 token", "停用"], ["重新连接"]],
  [
    { status: "login_paused", retry_at: "2026-11-02T10:00:00Z" },
    "已暂停登录，将在",
    ["更换 token", "停用"],
    ["重新连接"],
  ],
  [
    { status: "token_invalid" },
    "token 失效或账号被封，请到 Discord 确认",
    ["更换 token", "重新连接", "停用"],
    [],
  ],
  [
    { status: "token_invalid", last_error: "token_undecryptable" },
    "token 无法解密，请检查主密钥或重新填写",
    ["更换 token"],
    [],
  ],
  [
    { status: "verification_required" },
    "Discord 要求验证，请用官方客户端处理",
    ["重新连接", "更换 token", "停用"],
    [],
  ],
  [
    { status: "account_mismatch" },
    "这个 token 属于另一个 Discord 账号",
    ["确认换成这个账号", "更换 token", "停用"],
    [],
  ],
  [{ status: "banned" }, "账号已被封", ["更换 token", "停用"], ["重新连接"]],
  [{ status: "disabled" }, "已停用", ["启用", "更换 token"], ["停用"]],
];

describe("AccountPanel", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    put.mockReset();
  });

  // covers: AC-8, AC-9, AC-10, AC-12
  it.each(CASES)("状态 %j 显示对应的文案和操作", async (overrides, text, present, absent) => {
    showAccount(overrides);

    expect(await screen.findByText(new RegExp(text))).toBeInTheDocument();
    for (const name of present) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    for (const name of absent) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  // covers: AC-15
  it("collector_stale 叠加显示采集进程无响应", async () => {
    showAccount({ status: "online", collector_stale: true });

    expect(await screen.findByText("采集进程无响应")).toBeInTheDocument();
    expect(screen.getByText("在线（隐身）")).toBeInTheDocument();
  });

  // covers: AC-1, AC-4
  it("没有账号时显示小号提示，提交后清空 token", async () => {
    get.mockResolvedValue({ data: { account: null }, ...jsonResponse(200) });
    post.mockResolvedValue({ data: ACCOUNT, ...jsonResponse(201) });
    renderWithQuery(<AccountPanel />);

    expect(await screen.findByText(/selfbot 违反 Discord 服务条款/)).toBeInTheDocument();
    const token = screen.getByLabelText("Token") as HTMLInputElement;
    expect(token.type).toBe("password");
    expect(token.autocomplete).toBe("off");

    fireEvent.change(screen.getByLabelText("备注名"), { target: { value: "小号" } });
    fireEvent.change(token, { target: { value: "secret-token" } });
    fireEvent.click(screen.getByRole("button", { name: "添加账号" }));

    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/api/discord/account", {
        body: { label: "小号", token: "secret-token" },
      }),
    );
    expect(await screen.findByText("alt_user")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("secret-token")).not.toBeInTheDocument();
  });

  // covers: AC-3
  it("已有账号的错误用统一文案显示", async () => {
    get.mockResolvedValue({ data: { account: null }, ...jsonResponse(200) });
    post.mockResolvedValue({
      error: { error: { code: "account_exists", message: "x" } },
      ...jsonResponse(409),
    });
    renderWithQuery(<AccountPanel />);

    fireEvent.change(await screen.findByLabelText("备注名"), { target: { value: "小号" } });
    fireEvent.change(screen.getByLabelText("Token"), { target: { value: "secret-token" } });
    fireEvent.click(screen.getByRole("button", { name: "添加账号" }));

    expect(await screen.findByText("已经有一个账号了")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("secret-token")).not.toBeInTheDocument();
  });

  // covers: AC-11
  it("确认换身份要二次确认", async () => {
    showAccount({ status: "account_mismatch" });
    post.mockResolvedValue({ data: { ...ACCOUNT, status: "connecting" }, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "确认换成这个账号" }));
    expect(post).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认更换" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/discord/account/switch-identity"));
  });

  // covers: AC-10
  it("更换 token 从不回填，保存后收起", async () => {
    showAccount({ status: "online" });
    put.mockResolvedValue({ data: ACCOUNT, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "更换 token" }));
    const input = screen.getByLabelText("新 Token") as HTMLInputElement;
    expect(input.value).toBe("");

    fireEvent.change(input, { target: { value: "next-token" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(put).toHaveBeenCalledWith("/api/discord/account/token", {
        body: { token: "next-token" },
      }),
    );
    await waitFor(() => expect(screen.queryByLabelText("新 Token")).not.toBeInTheDocument());
  });

  // covers: AC-12
  it("停用直接发请求并更新状态", async () => {
    showAccount({ status: "online" });
    post.mockResolvedValue({ data: { ...ACCOUNT, status: "disabled" }, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "停用" }));

    expect(await screen.findByText("已停用")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/discord/account/disable");
  });
});
