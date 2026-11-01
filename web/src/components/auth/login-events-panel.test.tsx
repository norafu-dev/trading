import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginEventsPanel } from "./login-events-panel";
import { jsonResponse, renderWithQuery } from "./test-utils";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

describe("LoginEventsPanel", () => {
  beforeEach(() => get.mockReset());

  // covers: AC-11
  it("请求 50 条，显示中文结果、IP 和完整的浏览器标识", async () => {
    const ua = "x".repeat(300);
    get.mockResolvedValue({
      data: [
        {
          occurred_at: "2026-11-01T10:00:00Z",
          ip: "203.0.113.7",
          user_agent: ua,
          outcome: "bad_credentials",
        },
        { occurred_at: "2026-11-01T09:00:00Z", ip: null, user_agent: null, outcome: "reset" },
      ],
      ...jsonResponse(200),
    });
    renderWithQuery(<LoginEventsPanel />);

    expect(await screen.findByText("密码或验证码错误")).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith("/api/auth/login-events", {
      params: { query: { limit: 50 } },
    });
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    expect(screen.getByText("服务器命令")).toBeInTheDocument();
    expect(screen.getByText("重置")).toBeInTheDocument();
    expect(screen.getByTitle(ua)).toBeInTheDocument();
  });
});
