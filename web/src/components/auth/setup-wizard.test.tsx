import { screen, waitFor } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { passwordLength, passwordProblem } from "./password-rules";
import { SetupWizard } from "./setup-wizard";
import { jsonResponse, renderWithQuery } from "./test-utils";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  hardNavigate: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: mocks.replace }) }));
vi.mock("@/lib/api/client", () => ({ api: { GET: mocks.get, POST: mocks.post } }));
vi.mock("@/lib/auth/navigate", () => ({ hardNavigate: mocks.hardNavigate }));

const PROVISIONING = {
  otpauth_uri: "otpauth://totp/Harvester:dashboard?secret=ABC&issuer=Harvester",
  secret: "ABCDEFGHIJKLMNOP",
};
const PASSWORD = "correct horse battery";

async function toPasswordStep(user: UserEvent) {
  mocks.post.mockResolvedValueOnce({ data: PROVISIONING, ...jsonResponse(200) });
  await user.type(screen.getByLabelText("设置码"), "  the-code  ");
  await user.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByText("第 2 步：设置密码");
}

async function toConfirmStep(user: UserEvent) {
  await toPasswordStep(user);
  await user.type(screen.getByLabelText("密码"), PASSWORD);
  await user.type(screen.getByLabelText("再输入一次"), PASSWORD);
  await user.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByText("第 3 步：添加到验证器");
  await user.click(screen.getByRole("button", { name: "下一步" }));
  await screen.findByText("第 4 步：确认验证码");
}

describe("SetupWizard", () => {
  beforeEach(() => {
    for (const mock of Object.values(mocks)) mock.mockReset();
    mocks.get.mockResolvedValue({ data: { state: "setup_required" }, ...jsonResponse(200) });
  });

  // covers: AC-4
  it("走完四步才提交一次 complete，然后进首页", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SetupWizard />);
    await toConfirmStep(user);
    expect(mocks.post).toHaveBeenCalledOnce();
    expect(mocks.post).toHaveBeenCalledWith("/api/auth/setup/begin", {
      body: { setup_code: "the-code" },
    });

    mocks.post.mockResolvedValueOnce(jsonResponse(204));
    await user.type(screen.getByLabelText("验证码"), "123456");
    await user.click(screen.getByRole("button", { name: "完成设置" }));
    await waitFor(() => expect(mocks.hardNavigate).toHaveBeenCalledWith("/"));
    expect(mocks.post).toHaveBeenLastCalledWith("/api/auth/setup/complete", {
      body: { setup_code: "the-code", password: PASSWORD, totp_code: "123456" },
    });
  });

  // covers: AC-4
  it("第 3 步显示二维码和可手动输入的密钥", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SetupWizard />);
    await toPasswordStep(user);
    await user.type(screen.getByLabelText("密码"), PASSWORD);
    await user.type(screen.getByLabelText("再输入一次"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "下一步" }));
    expect(await screen.findByText(PROVISIONING.secret)).toBeInTheDocument();
    expect(screen.getByTitle("添加到验证器的二维码")).toBeInTheDocument();
  });

  // covers: AC-4
  it("设置码错了留在第 1 步并提示", async () => {
    const user = userEvent.setup();
    mocks.post.mockResolvedValueOnce({
      error: { error: { code: "setup_code_invalid" } },
      ...jsonResponse(403),
    });
    renderWithQuery(<SetupWizard />);
    await user.type(screen.getByLabelText("设置码"), "bad");
    await user.click(screen.getByRole("button", { name: "下一步" }));
    expect(await screen.findByText("设置码无效")).toBeInTheDocument();
    expect(screen.getByLabelText("设置码")).toBeInTheDocument();
  });

  // covers: AC-4
  it("密码太短或两次不一样时不往下走", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SetupWizard />);
    await toPasswordStep(user);
    await user.type(screen.getByLabelText("密码"), "short");
    await user.type(screen.getByLabelText("再输入一次"), "short");
    await user.click(screen.getByRole("button", { name: "下一步" }));
    expect(await screen.findByText(/密码长度要在 12 到 128/)).toBeInTheDocument();
    expect(screen.getByText("第 2 步：设置密码")).toBeInTheDocument();
  });

  // covers: AC-4
  it("验证码错了留在第 4 步，可以再试", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SetupWizard />);
    await toConfirmStep(user);
    mocks.post.mockResolvedValueOnce({
      error: { error: { code: "totp_invalid" } },
      ...jsonResponse(400),
    });
    await user.type(screen.getByLabelText("验证码"), "000000");
    await user.click(screen.getByRole("button", { name: "完成设置" }));
    expect(await screen.findByText("验证码不对")).toBeInTheDocument();
    expect(screen.getByText("第 4 步：确认验证码")).toBeInTheDocument();
    expect(mocks.hardNavigate).not.toHaveBeenCalled();
  });

  // covers: AC-3
  it("设置码中途过期时回到第 1 步", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SetupWizard />);
    await toConfirmStep(user);
    mocks.post.mockResolvedValueOnce({
      error: { error: { code: "setup_code_invalid" } },
      ...jsonResponse(403),
    });
    await user.type(screen.getByLabelText("验证码"), "123456");
    await user.click(screen.getByRole("button", { name: "完成设置" }));
    expect(await screen.findByText("第 1 步：输入设置码")).toBeInTheDocument();
    expect(screen.getByText("设置码无效")).toBeInTheDocument();
  });
});

describe("passwordProblem", () => {
  // covers: AC-4
  it("按 NFKC 之后的码点数算长度", () => {
    expect(passwordLength("ａ")).toBe(1);
    expect(passwordLength("🎧🎧")).toBe(2);
    expect(passwordLength("ﬃ")).toBe(3);
    expect(passwordProblem("a".repeat(11), "a".repeat(11))).toMatch(/长度/);
    expect(passwordProblem("a".repeat(12), "a".repeat(12))).toBeNull();
    expect(passwordProblem("a".repeat(129), "a".repeat(129))).toMatch(/长度/);
    expect(passwordProblem("a".repeat(12), "b".repeat(12))).toMatch(/不一样/);
  });
});
