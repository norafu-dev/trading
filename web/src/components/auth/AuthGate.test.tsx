import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./AuthGate";
import { jsonResponse, renderWithQuery } from "./test-utils";

const { replace, get } = vi.hoisted(() => ({ replace: vi.fn(), get: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

function renderGate() {
  renderWithQuery(
    <AuthGate>
      <div>外壳和数据</div>
    </AuthGate>,
  );
}

function stateIs(state: string) {
  get.mockResolvedValue({ data: { state }, ...jsonResponse(200) });
}

describe("AuthGate", () => {
  beforeEach(() => {
    replace.mockReset();
    get.mockReset();
    window.history.pushState({}, "", "/channels?id=1");
  });

  // covers: AC-13
  it("确认状态之前只有加载动画，不出现外壳", () => {
    get.mockReturnValue(new Promise(() => {}));
    renderGate();
    expect(screen.getByRole("status", { name: "加载中" })).toBeInTheDocument();
    expect(screen.queryByText("外壳和数据")).not.toBeInTheDocument();
  });

  // covers: AC-13
  it("已登录才渲染外壳", async () => {
    stateIs("authenticated");
    renderGate();
    expect(await screen.findByText("外壳和数据")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  // covers: AC-13
  it("未设置跳 /setup，不渲染外壳", async () => {
    stateIs("setup_required");
    renderGate();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/setup"));
    expect(screen.queryByText("外壳和数据")).not.toBeInTheDocument();
  });

  // covers: AC-13
  it("未登录跳 /login，带上原地址", async () => {
    stateIs("unauthenticated");
    renderGate();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login?next=%2Fchannels%3Fid%3D1"));
    expect(screen.queryByText("外壳和数据")).not.toBeInTheDocument();
  });

  // covers: AC-13
  it("网络错误时重试 2 次，然后显示无法连接和重试，不跳转", async () => {
    get.mockRejectedValue(new TypeError("Failed to fetch"));
    renderGate();
    expect(await screen.findByText("无法连接服务器")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(3);
    expect(replace).not.toHaveBeenCalled();
    expect(screen.queryByText("外壳和数据")).not.toBeInTheDocument();

    stateIs("authenticated");
    await userEvent.setup().click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("外壳和数据")).toBeInTheDocument();
  });

  // covers: AC-13
  it("503 也显示无法连接，不跳转", async () => {
    get.mockResolvedValue({
      error: { error: { code: "database_unavailable" } },
      ...jsonResponse(503),
    });
    renderGate();
    expect(await screen.findByText("无法连接服务器")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
