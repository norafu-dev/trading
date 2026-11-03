import { describe, expect, it, vi } from "vitest";

import { createAuthExpiryMiddleware } from "./auth-expiry";

async function run(path: string, status: number) {
  const navigate = vi.fn();
  const { onResponse } = createAuthExpiryMiddleware(navigate);
  const response = new Response(null, { status });
  const result = await onResponse?.({
    request: new Request(`http://localhost${path}`),
    response,
    // 中间件只用到 request 和 response。
  } as never);
  return { navigate, result, response };
}

describe("createAuthExpiryMiddleware", () => {
  // covers: AC-14
  it("受保护接口返回 401 时跳登录页，带当前地址和 reason=expired", async () => {
    window.history.pushState({}, "", "/channels?id=1");
    const { navigate, result, response } = await run("/api/health", 401);
    expect(navigate).toHaveBeenCalledWith("/login?next=%2Fchannels%3Fid%3D1&reason=expired");
    expect(result).toBe(response);
  });

  // covers: AC-14
  it("login-events 需要会话，它的 401 也算过期", async () => {
    const { navigate } = await run("/api/auth/login-events", 401);
    expect(navigate).toHaveBeenCalledOnce();
  });

  // covers: AC-5, AC-14
  it("登录接口的 401 是密码错了，不跳转", async () => {
    const { navigate } = await run("/api/auth/login", 401);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("其他状态码不跳转", async () => {
    const { navigate } = await run("/api/health", 503);
    expect(navigate).not.toHaveBeenCalled();
  });
});
