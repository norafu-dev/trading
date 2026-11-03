import type { Middleware } from "openapi-fetch";

import { currentPath, hardNavigate } from "@/lib/auth/navigate";
import { loginHref } from "@/lib/auth/safe-next";

// 这些接口的 401 是正常的业务回答（比如密码错了），不代表会话过期。
// /api/auth/login-events 不在其中：它需要会话，401 就是过期（AC-14）。
const AUTH_PATHS_WITHOUT_SESSION = new Set([
  "/api/auth/state",
  "/api/auth/login",
  "/api/auth/setup/begin",
  "/api/auth/setup/complete",
  "/api/auth/logout",
]);

export function createAuthExpiryMiddleware(navigate: (href: string) => void = hardNavigate) {
  const middleware: Middleware = {
    onResponse({ request, response }) {
      if (
        response.status === 401 &&
        !AUTH_PATHS_WITHOUT_SESSION.has(new URL(request.url).pathname)
      ) {
        navigate(loginHref(currentPath(), "expired"));
      }
      return response;
    },
  };
  return middleware;
}
