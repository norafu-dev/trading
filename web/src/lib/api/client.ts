import createClient from "openapi-fetch";

import type { paths } from "./schema";

// Same origin as the dashboard (Caddy routes /api/* to FastAPI), so no base URL or CORS.
export const api = createClient<paths>({ baseUrl: "" });
