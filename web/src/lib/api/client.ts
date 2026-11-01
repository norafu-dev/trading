import createClient from "openapi-fetch";

import { createAuthExpiryMiddleware } from "./auth-expiry";
import type { paths } from "./schema";

// 和 dashboard 同源（Caddy 把 /api/* 转给 FastAPI），所以不需要 base URL，也没有跨域。
export const api = createClient<paths>({ baseUrl: "" });

api.use(createAuthExpiryMiddleware());
