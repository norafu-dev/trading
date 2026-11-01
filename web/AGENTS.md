<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

- 设计规范：所有界面按 [DESIGN.md](DESIGN.md) 做（美术方向和 build mandate）；token 的具体数值在 `src/app/globals.css`。
- `*.dev.tsx` 页面只在 `next dev` 下算路由（见 `next.config.ts`），生产构建不包含它们和它们引入的假数据。
