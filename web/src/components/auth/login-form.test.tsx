import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login-form";
import { jsonResponse, renderWithQuery } from "./test-utils";

const mocks = vi.hoisted(() => ({
  replace: vi.fn(),
  get: vi.fn(),
  post: vi.fn(),
  hardNavigate: vi.fn(),
  search: "",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/lib/api/client", () => ({ api: { GET: mocks.get, POST: mocks.post } }));
vi.mock("@/lib/auth/navigate", () => ({ hardNavigate: mocks.hardNavigate }));

async function fillAndSubmit() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("密码"), "correct horse battery");
  await user.type(screen.getByLabelText("验证码"), "123456");
  await user.click(screen.getByRole("button", { name: "登录" }));
}

function stateIs(state: string) {
  mocks.get.mockResolvedValue({ data: { state }, ...jsonResponse(200) });
}

describe("LoginForm", () => {
  beforeEach(() => {
    for (const mock of [mocks.replace, mocks.get, mocks.post, mocks.hardNavigate]) mock.mockReset();
    mocks.search = "";
    stateIs("unauthenticated");
  });

  // covers: AC-5, AC-13
  it("成功后回到 next，密码和验证码一起提交", async () => {
    mocks.search = "next=%2Fchannels%3Fid%3D1";
    mocks.post.mockResolvedValue(jsonResponse(204));
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    await waitFor(() => expect(mocks.hardNavigate).toHaveBeenCalledWith("/channels?id=1"));
    expect(mocks.post).toHaveBeenCalledWith("/api/auth/login", {
      body: { password: "correct horse battery", totp_code: "123456" },
    });
  });

  // covers: AC-13
  it("next 指向站外时回首页", async () => {
    mocks.search = "next=%2F%2Fevil.com";
    mocks.post.mockResolvedValue(jsonResponse(204));
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    await waitFor(() => expect(mocks.hardNavigate).toHaveBeenCalledWith("/"));
  });

  // covers: AC-5
  it("失败时只提示密码或验证码不对，并清空验证码", async () => {
    mocks.post.mockResolvedValue({
      error: { error: { code: "bad_credentials" } },
      ...jsonResponse(401),
    });
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    expect(await screen.findByText("密码或验证码不对。")).toBeInTheDocument();
    expect(screen.getByLabelText("验证码")).toHaveValue("");
    expect(mocks.hardNavigate).not.toHaveBeenCalled();
  });

  // covers: AC-9
  it("被限速时显示要等多久", async () => {
    mocks.post.mockResolvedValue({
      error: { error: { code: "rate_limited", retry_after_seconds: 60 } },
      ...jsonResponse(429),
    });
    renderWithQuery(<LoginForm />);
    await fillAndSubmit();
    expect(await screen.findByText("请 1 分钟后再试。")).toBeInTheDocument();
  });

  // covers: AC-14
  it("reason=expired 时提示登录已过期", () => {
    mocks.search = "reason=expired";
    renderWithQuery(<LoginForm />);
    expect(screen.getByText("登录已过期，请重新登录")).toBeInTheDocument();
  });

  // covers: AC-13
  it("已登录时打开登录页直接去首页", async () => {
    stateIs("authenticated");
    renderWithQuery(<LoginForm />);
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/"));
  });

  // covers: AC-13
  it("未设置时去设置页", async () => {
    stateIs("setup_required");
    renderWithQuery(<LoginForm />);
    await waitFor(() => expect(mocks.replace).toHaveBeenCalledWith("/setup"));
  });
});
