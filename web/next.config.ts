import type { NextConfig } from "next";

// `*.dev.tsx` pages (the /dev/preview gallery) only exist under `next dev`; a production
// build never treats them as routes, so they and their fake data stay out of `out/`.
const isDev = process.env.NODE_ENV !== "production";

// Static export served by Caddy; every piece of data comes from the FastAPI /api/* routes.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
  pageExtensions: isDev ? ["tsx", "ts", "dev.tsx"] : ["tsx", "ts"],
};

export default nextConfig;
