# Harvester

只给自己用的 Discord 频道采集器：`collector` 进程用小号挂在 Discord 上采集消息，`api` 进程给 Next.js dashboard 提供 REST 和 SSE，两者只通过 PostgreSQL 协作。

## Stack

- **后端**：Python 3.13（`uv`），FastAPI（SSE），SQLAlchemy 2.x async + `asyncpg` + Alembic，`discord.py-self`，`pydantic-settings`，`structlog`
- **前端**：Next.js 16（App Router，`output: 'export'` 静态导出），React 19，TypeScript，TanStack Query + `openapi-fetch`（类型由 `openapi-typescript` 从 `backend/openapi.json` 生成），Tailwind CSS v4 + shadcn/ui（Base UI）+ TanStack Virtual，`pnpm`
- **数据库**：PostgreSQL 18 + `pgvector`；进程间用 `LISTEN/NOTIFY` 和任务表（`FOR UPDATE SKIP LOCKED`）
- **运行与发布**：Docker Compose + Caddy（HTTPS，同源），AWS Lightsail 单机；GitHub Actions 推镜像到 GHCR，`deploy/deploy.sh` 按改动路径只更新受影响的服务
- 完整架构、不变量和发布规则见 [spec 0001](docs/specs/0001-stack-and-architecture/index.md)

## Build approach

**Tracer Bullet**：先打通一条从 Discord 到存储再到 dashboard 的真实链路，之后每个切片加粗其中一段，始终端到端可用。

## Commands

```bash
docker compose up -d                                   # 本地 postgres + caddy，浏览器开 http://localhost:8080
cd backend && uv sync && uv run alembic upgrade head   # 安装依赖并迁移
uv run python -m harvester.api                         # api，:8000（只开一个 worker）
uv run python -m harvester.collector                   # collector
cd web && pnpm install && pnpm dev                     # 前端，:3000
uv run ruff check . && uv run ruff format --check . && uv run pyright   # 后端检查
pnpm typecheck && pnpm build                           # 前端检查和构建
uv run pytest                                          # 后端测试（连真实 PostgreSQL）
pnpm lint && pnpm test && pnpm check:contrast          # 前端 Biome、Vitest、设计 token 对比度检查
uv run python -m harvester.crypto genkey               # 生成 TOKEN_ENCRYPTION_KEY
uv run python -m harvester.api.setup_code              # 第一次打开 dashboard 前生成设置码
uv run python -m harvester.api.auth_reset              # 重置 dashboard 登录凭据和所有会话
uv run python -m harvester.api.export_openapi && cd ../web && pnpm gen:api   # 改接口后同步前端类型
```

## Specs

存放在 `docs/specs/`，每个 spec 是一个目录：`docs/specs/NNNN-title/index.md`（加 `rationale.md`）。路线图在 `docs/scope/scope.md`。

## Rules

- **Functional 风格**：默认写纯函数，副作用（数据库、Discord、网络、时间）推到边缘并显式传入；能用函数就不用类（SQLAlchemy 模型、Pydantic 模型、React 组件除外）。模块级变量只能是常量，不共享可变状态。
- 数据不就地修改：Python 用 `frozen` dataclass、`tuple`、`Mapping`；TS 用 `readonly` 和 `const`，返回新对象。
- 可预期的失败（token 失效、记录不存在、校验失败）用显式返回值表达（Python 返回联合类型，TS 返回判别联合），只有真正的意外才抛异常。
- **类型**：pyright `strict`，不允许隐式 `Any`；缺类型的第三方库（如 `discord.py-self`）只在边界一处包一层带类型的函数。TypeScript 保持 `strict`，不用 `any`。
- **目录**：沿用脚手架。后端按进程和基础设施分（`collector/`、`api/`、`db/`），进程内部按功能拆文件；前端 `src/app/` 放页面，组件和逻辑按功能放在一起，接口客户端在 `src/lib/api/`。
- **错误处理统一**：后端定义一组项目异常类型，api 用一个统一的异常处理器转成固定格式的 JSON 错误；前端在一处把接口错误转成界面状态，不在每个组件里各写一套。
- 配置全部走 `harvester.config` 的 `pydantic-settings`，启动时校验，缺配置直接报错退出；密钥绝不进 git、日志或前端代码。
- 前端只用具名导出；Next.js 要求的 `page.tsx`、`layout.tsx` 等文件除外。
- 表结构只通过 Alembic 迁移修改；进程之间只通过 PostgreSQL 交流（其余不变量见 spec 0001）。
- 代码注释一律用简体中文写（标识符、日志、错误码等代码本身保持英文）。
- Commit message 用 Conventional Commits（`feat:`、`fix:`、`chore:`）。

## Tooling

由 `/develop tooling` 按这里的选择安装：
- **后端**：`ruff` 做 lint 和格式化，规则集 `E, F, I, B, UP, SIM, ASYNC, RUF`；`pyright` 设为 `strict`。
- **前端**：Biome 做 lint 和格式化（不用 ESLint 和 Prettier）。
- **提交前**：`pre-commit` 框架，对改动的文件跑 ruff 和 Biome，再跑 pyright 和 `tsc --noEmit`。
- **测试**：单元加集成测试。后端 pytest，集成测试连真实 PostgreSQL；前端 Vitest。新功能带测试。
- **CI**：跑 ruff、pyright、pytest、OpenAPI 类型同步检查、`pnpm lint`（Biome）、`pnpm typecheck`、Vitest、`pnpm build` 和 Docker 镜像构建。

## Git

- integration: on
- branch prefix: feat/
- commit: per-milestone

## Agent skills

- [fastapi](.claude/skills/fastapi/)：`fastapi/fastapi`，FastAPI、Pydantic、SSE 的写法（本项目用 SQLAlchemy 和 pyright，不用 skill 推荐的 SQLModel 和 `ty`）
- [vercel-react-best-practices](.claude/skills/vercel-react-best-practices/)：`vercel-labs/agent-skills`，React 和 Next.js 的性能惯例
- [tanstack-query](.claude/skills/tanstack-query/)：`tanstack-skills/tanstack-skills`，前端取数、缓存和 SSE 之外的服务端状态
- [postgresql-table-design](.claude/skills/postgresql-table-design/)：`wshobson/agents`，表结构、索引和约束设计
- [shadcn](.claude/skills/shadcn/)：`shadcn-ui/ui`，shadcn/ui 组件的添加、组合和样式
- [tailwind-4-docs](.claude/skills/tailwind-4-docs/)：`lombiq/tailwind-agent-skills`，Tailwind CSS v4 的文档和用法
- [vitest](.claude/skills/vitest/)：`antfu/skills`，Vitest 的写法、mock 和覆盖率
- [python-testing-patterns](.claude/skills/python-testing-patterns/)：`wshobson/agents`，pytest 的 fixture、mock 和测试模式
- [security-review](.claude/skills/security-review/)：`getsentry/skills`，安全审查，适合认证、加密和 token 相关的代码

Declined: `tanstack-skills/tanstack-skills@tanstack-virtual`

Declined: Lightsail 部署 skill (`aws/agent-toolkit-for-aws`)，其他未核实的社区 skill，全部 MCP 服务器（GitHub、Next.js DevTools、Postgres）

## Context files

- [web/AGENTS.md](web/AGENTS.md)（Next.js 自动维护的提示：这个版本的 Next.js 有破坏性改动，写代码前先读 `node_modules/next/dist/docs/`）

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
