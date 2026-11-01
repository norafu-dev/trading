import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// 单元和渲染测试默认跑在 jsdom 里；需要加载 WASM 的解析器测试在文件顶部改用 node 环境。
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}", "scripts/**/*.test.ts"],
  },
});
