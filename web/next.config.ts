import type { NextConfig } from "next";

// Static export served by Caddy; every piece of data comes from the FastAPI /api/* routes.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
};

export default nextConfig;
