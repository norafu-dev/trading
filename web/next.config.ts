import type { NextConfig } from "next";

// `*.dev.tsx` 页面（/dev/preview 预览页）只在 `next dev` 下存在；生产构建根本不把它们当路由，
// 所以页面和它引入的假数据都不会进 `out/`。
const isDev = process.env.NODE_ENV !== "production";

// 静态导出，由 Caddy 提供；所有数据都来自 FastAPI 的 /api/* 接口。
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: false,
  pageExtensions: isDev ? ["tsx", "ts", "dev.tsx"] : ["tsx", "ts"],
};

export default nextConfig;
