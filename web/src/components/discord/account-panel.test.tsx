import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import { SYSTEM_STATUS, systemStatusResponse } from "@/components/system/test-fixtures";

import { AccountPanel } from "./account-panel";
import type { DiscordAccount } from "./use-discord-account";

const { get, post, put, systemGet } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  systemGet: vi.fn(),
}));
// 系统状态单独分流，下面的用例只管账号接口。
vi.mock("@/lib/api/client", () => ({
  api: {
    GET: (path: string, ...rest: unknown[]) =>
      path === "/api/system/status" ? systemGet() : get(path, ...rest),
    POST: post,
    PUT: put,
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
    systemGet.mockReset();
    systemGet.mockResolvedValue(systemStatusResponse());
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

  // covers: AC-8, AC-9
  it("重新连接发请求后回到正在连接", async () => {
    showAccount({ status: "token_invalid" });
    post.mockResolvedValue({ data: { ...ACCOUNT, status: "connecting" }, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "重新连接" }));

    expect(await screen.findByText("正在连接")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/discord/account/reconnect");
  });

  // covers: AC-12
  it("启用发请求后回到正在连接", async () => {
    showAccount({ status: "disabled" });
    post.mockResolvedValue({ data: { ...ACCOUNT, status: "connecting" }, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "启用" }));

    expect(await screen.findByText("正在连接")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/discord/account/enable");
  });

  // covers: AC-13
  it("操作被后端拒绝时显示统一文案，状态不变", async () => {
    showAccount({ status: "token_invalid" });
    post.mockResolvedValue({
      error: { error: { code: "invalid_account_state", message: "x" } },
      ...jsonResponse(409),
    });

    fireEvent.click(await screen.findByRole("button", { name: "重新连接" }));

    expect(await screen.findByText("现在不能这样操作")).toBeInTheDocument();
    expect(screen.getByText("token 失效或账号被封，请到 Discord 确认")).toBeInTheDocument();
  });

  // covers: AC-11
  it("取消换身份不发请求，确认框收起", async () => {
    showAccount({ status: "account_mismatch" });

    fireEvent.click(await screen.findByRole("button", { name: "确认换成这个账号" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByRole("button", { name: "确认更换" })).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  // covers: AC-4
  it("更换 token 失败时清空输入，表单留着并显示原因", async () => {
    showAccount({ status: "online" });
    put.mockResolvedValue({
      error: { error: { code: "token_format_invalid", message: "x" } },
      ...jsonResponse(422),
    });

    fireEvent.click(await screen.findByRole("button", { name: "更换 token" }));
    const input = screen.getByLabelText("新 Token") as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.autocomplete).toBe("off");
    fireEvent.change(input, { target: { value: "bad token" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("token 的格式不对")).toBeInTheDocument();
    expect(screen.getByLabelText("新 Token")).toHaveValue("");
  });

  it("取消更换 token 不发请求", async () => {
    showAccount({ status: "online" });

    fireEvent.click(await screen.findByRole("button", { name: "更换 token" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(screen.queryByLabelText("新 Token")).not.toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });

  it("改备注名预填当前值，保存后显示新名字", async () => {
    showAccount({ status: "online" });
    put.mockResolvedValue({ data: { ...ACCOUNT, label: "新名字" }, ...jsonResponse(200) });

    fireEvent.click(await screen.findByRole("button", { name: "改备注名" }));
    const input = screen.getByLabelText("备注名");
    expect(input).toHaveValue("小号");
    fireEvent.change(input, { target: { value: "新名字" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("heading", { name: "新名字" })).toBeInTheDocument();
    expect(put).toHaveBeenCalledWith("/api/discord/account/label", { body: { label: "新名字" } });
    expect(screen.queryByLabelText("备注名")).not.toBeInTheDocument();
  });

  // covers: AC-2
  it("没登录过时提示还没有登录过", async () => {
    showAccount({ discord_username: null });

    expect(await screen.findByText("还没有登录过")).toBeInTheDocument();
  });

  // covers: AC-15
  it("采集进程无响应时操作仍然可用", async () => {
    showAccount({ status: "online", collector_stale: true });

    expect(await screen.findByRole("button", { name: "停用" })).toBeEnabled();
  });

  // covers: AC-15
  it("状态放进 role=status，读屏能读到变化", async () => {
    showAccount({ status: "online" });

    await screen.findByText("alt_user");

    expect(screen.getByRole("status")).toHaveTextContent("在线（隐身）");
  });

  it("读取失败时显示错误和重试，重试成功后恢复", async () => {
    get.mockResolvedValue({ error: {}, ...jsonResponse(500) });
    renderWithQuery(<AccountPanel />);

    const retry = await screen.findByRole("button", { name: /重试/ }, { timeout: 5000 });
    get.mockResolvedValue({ data: { account: ACCOUNT }, ...jsonResponse(200) });
    fireEvent.click(retry);

    expect(await screen.findByText("alt_user")).toBeInTheDocument();
  });

  // covers: AC-4
  it("已有账号时不主动显示 token 输入框", async () => {
    showAccount({ status: "online" });

    await screen.findByText("alt_user");

    expect(screen.queryByLabelText(/token/i)).not.toBeInTheDocument();
  });

  // covers: spec 0009 AC-4
  it("账号卡显示采集进程最近心跳，悬停看完整时间", async () => {
    showAccount({});

    await screen.findByText(/采集进程最近心跳/);
    const beat = document.querySelector(
      `time[datetime="${SYSTEM_STATUS.collector.last_heartbeat_at}"]`,
    );
    if (!(beat instanceof HTMLElement)) throw new Error("没有心跳时间");
    expect(beat).toHaveAttribute("dateTime", SYSTEM_STATUS.collector.last_heartbeat_at);
    expect(beat.getAttribute("title")).toMatch(/2026/);
  });

  // covers: spec 0009 AC-4
  it("没有进程心跳时显示还没有心跳", async () => {
    systemGet.mockResolvedValue(
      systemStatusResponse({
        ...SYSTEM_STATUS,
        collector: { last_heartbeat_at: null, stale: true, has_account: true },
      }),
    );
    showAccount({ collector_stale: true });

    expect(await screen.findByText(/还没有心跳/)).toBeInTheDocument();
    expect(screen.getByText("采集进程无响应")).toBeInTheDocument();
  });
});
