# 0001. 技术栈与整体架构

**Date**: 2026-10-28
**Status**: Accepted

## Summary

这个项目用 Python 写后端，Next.js 写 dashboard 界面，PostgreSQL 存数据，全部用 Docker Compose 跑在一台 AWS Lightsail 东京服务器上（每月 $24）。采集进程（collector，用你的小号挂在 Discord 上）和 dashboard 的接口服务（api）是两个独立进程，只通过数据库协作，所以改 dashboard 不会让小号掉线重登。以后的 AI 分析和交易所下单已经预留好位置：各自是新的独立进程，数据库也已经带上向量检索（pgvector）。构建时按下面的「Proposed stack」和「项目结构」搭出空项目即可。

## Decision

**Chosen option**: Option 1: Python 多进程 + Next.js 静态前端 + PostgreSQL，单台 Lightsail 用 Docker Compose 运行

一个仓库，`backend/` 是一个 Python 包，提供 `collector` 和 `api` 两个启动入口（以后加 `analyzer`、`trader`）；`web/` 是 Next.js 静态导出的 dashboard；Caddy 统一对外提供 HTTPS；进程之间只通过 PostgreSQL（数据表、`LISTEN/NOTIFY`、任务表）协作。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `vercel-react-best-practices` (`vercel-labs/agent-skills`, `.claude/skills/vercel-react-best-practices/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`)

与 `fastapi` skill 的有意偏离（你已确认）：用 SQLAlchemy 2.x，不用 skill 推荐的 SQLModel；用 `pyright`，不用 skill 推荐的 `ty`。前端由 Caddy 提供，不用 skill 里的 `app.frontend()`，因为 Caddy 本来就要负责 HTTPS。其他部分按 skill 的惯例来（SSE 写法、uv、ruff）。

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Proposed stack

| Layer | Choice | Reason |
|---|---|---|
| 架构形态 | 一个代码仓库，多个进程：`collector`、`api`，以后加 `analyzer`、`trader` | 采集必须 24 小时不断线，和经常要改的 dashboard 分开；但仍是同一份代码和同一套模型，不是微服务 |
| 语言（后端） | Python 3.13 | 你最熟悉；满足所有依赖的版本要求（SQLAlchemy 2.1 要求 3.11 以上） |
| Discord 连接 | `discord.py-self` | Python 下唯一仍在维护的成熟 selfbot 库，支持历史消息和编辑删除事件 |
| API 框架 | FastAPI（自带 SSE） | 原生 async，和 selfbot 的 asyncio 一致；`fastapi.sse.EventSourceResponse` 直接做实时推送，不需要额外库 |
| 前端 | Next.js（App Router，`output: 'export'` 静态导出）+ React + TypeScript | 你指定的框架；静态导出后不需要常驻 Node 进程，私人 dashboard 也不需要服务端渲染 |
| 前端数据层 | TanStack Query + `openapi-fetch`，类型由 `openapi-typescript` 从 FastAPI 的 OpenAPI 生成 | 后端改字段，前端编译时就报错；缓存、加载、重试交给 TanStack Query |
| 实时推送 | collector 写入后 `NOTIFY` → api `LISTEN` → SSE → 浏览器 | 秒级延迟，不加 Redis；SSE 自带断线重连 |
| 主数据库 | PostgreSQL 18 + `pgvector` 扩展（镜像 `pgvector/pgvector:pg18`；构建时确认标签存在，不存在就用 `pg17`） | 多进程并发写、下单所需的事务、AI 向量检索、进程间通知，一个库全包 |
| 数据访问 | SQLAlchemy 2.x（async）+ `asyncpg` 驱动 + Alembic 迁移 | 最成熟的组合；collector 和 api 共用一套模型；pgvector 有官方 SQLAlchemy 支持 |
| 后台任务 | PostgreSQL 任务表，collector 用 `SELECT ... FOR UPDATE SKIP LOCKED` 领取 | 历史采集必须用 collector 里已登录的账号执行；进度和断点天然存在库里 |
| 认证 | 由第 5 项「Dashboard 访问保护」的 spec 决定 | 约束：认证在 FastAPI 一侧完成，前后端同源，用 cookie 会话（见下方不变量） |
| 文件存储 | 由第 12 项「附件与图片保存」的 spec 决定 | 这一轮不需要 |
| 搜索 | 由第 13 项「消息搜索与筛选」的 spec 决定 | 默认先用 PostgreSQL 自带能力，中文分词在那里单独决定 |
| 配置与密钥 | `pydantic-settings` 读取环境变量；服务器上是 `.env` 文件（权限 600，不进 git） | 单人单机最实用；加了下单之后可以重新评估 |
| 日志 | `structlog` 输出 JSON 到 stdout，由 Docker 收集 | 结构化字段方便查；在一处统一过滤 token 和密钥 |
| 错误发现 | 先只看日志 + dashboard 心跳（心跳由第 9 项实现） | 单人工具先不接外部服务；加下单时再引入告警 |
| 反向代理 / HTTPS | Caddy（自动申请证书），你自己的域名 | 配置只有几行；同一个域名下分流前端和 API |
| 托管 | AWS Lightsail，东京区域（有大阪就选大阪），2 vCPU / 4 GB / 80 GB SSD，$24/月，x86 | 大厂、固定月价全包（含公网 IPv4 和 4 TB 流量），离大阪近 |
| 运行方式 | Docker Compose，所有服务 `restart: unless-stopped` | 本地和服务器一致；进程崩溃或服务器重启后自动恢复 |
| 发布 | GitHub Actions 构建镜像推到 GHCR（私有）→ 通过 SSH 登录服务器，按改动的路径只更新受影响的服务（见「发布规则」） | 小服务器不用自己编译 Next.js；可以按 git sha 回滚；只改 api 时小号不掉线 |
| Python 工具链 | `uv`（依赖和虚拟环境）、`ruff`（lint 和格式化）、`pyright`（类型检查） | 快、工具少；规则细节由第 2 项 `/audit` 定 |
| 前端工具链 | `pnpm` | 快、省磁盘 |

### 进程与数据流

```
                       Discord（用户账号网关 + REST）
                                 │
                        ┌────────▼────────┐
                        │   collector     │  单实例，持有 Discord 会话
                        │ (discord.py-self)│  执行历史采集任务
                        └───┬────────▲────┘
              INSERT + NOTIFY│        │领取任务 (SKIP LOCKED)
                        ┌───▼────────┴────┐
                        │  PostgreSQL     │  消息、频道开关、任务表、
                        │  + pgvector     │  以后的向量和订单
                        └───┬────────▲────┘
                      LISTEN│        │读写（开关、建任务）
                        ┌───▼────────┴────┐
                        │      api        │  FastAPI：REST + SSE
                        └────────▲────────┘
                                 │ /api/*
 浏览器 ──HTTPS──► Caddy ────────┤
                                 │ 其余路径
                         web/out 静态文件（Next.js 导出）

 以后：analyzer（AI 分析）、trader（下单）= 新的独立进程，同样只连 PostgreSQL
```

### 项目结构

```
.
├── backend/
│   ├── pyproject.toml          # uv 管理；一个包，用 python -m 启动各入口
│   ├── Dockerfile              # python:3.13-slim，uv sync --frozen --no-dev
│   ├── openapi.json            # 由 export_openapi 导出并提交，前端类型从它生成
│   ├── alembic.ini
│   ├── migrations/             # Alembic 迁移（async env.py，导入共用 Base.metadata）
│   └── src/harvester/
│       ├── config.py           # pydantic-settings
│       ├── logging.py          # structlog 配置 + 敏感字段过滤
│       ├── db/                 # 引擎、会话、共用 ORM 模型、专用 asyncpg 连接工具
│       ├── collector/          # 入口：python -m harvester.collector
│       └── api/
│           ├── main.py         # app 工厂和 lifespan（启动 LISTEN 任务）
│           ├── __main__.py     # 入口：python -m harvester.api（uvicorn.run）
│           └── export_openapi.py
├── web/                        # Next.js（App Router，静态导出）
│   ├── package.json            # packageManager 字段固定 pnpm 版本
│   ├── .nvmrc                  # 固定 Node 版本
│   └── src/lib/api/            # openapi-typescript 生成的类型 + openapi-fetch 客户端
├── deploy/
│   ├── Caddyfile               # 线上：域名 + 自动 HTTPS
│   ├── Caddyfile.dev           # 本地：:8080 纯 HTTP
│   ├── caddy.Dockerfile        # 多阶段：node 构建 web/out → 复制到 /srv
│   ├── compose.prod.yml
│   └── deploy.sh               # 服务器上执行，按发布规则更新服务
├── compose.yml                 # 本地开发：postgres + caddy
├── .github/workflows/          # PR 检查 + main 分支发布
└── docs/
```

Compose 服务和启动顺序：`postgres`（`pg_isready` 健康检查）→ `migrate`（一次性运行 `alembic upgrade head`；`depends_on: postgres: condition: service_healthy`）→ `api` 和 `collector`（`depends_on: migrate: condition: service_completed_successfully`）→ `caddy`（依赖 `api` 启动，不等它健康）。`backend` 一个镜像，不同服务只换启动命令；`web` 在 caddy 镜像里构建并提供。

### 运行时约定

这些是复查时补上的，避免搭空项目时两个人写出不同的东西：

- **api 进程**：`uvicorn.run("harvester.api.main:app", host="0.0.0.0", port=8000, proxy_headers=True)`，**只开一个 worker**（多个 worker 会各自 LISTEN、各自推送，SSE 会错乱）。
- **LISTEN 连接**：api 在 lifespan 里用一条**不经过 SQLAlchemy 连接池**的专用连接（`asyncpg.connect` + `add_listener`），断开后自动重连。asyncpg 的连接串要从 `DATABASE_URL` 去掉 `+asyncpg`。
- **SSE**：每个事件带 `id`（消息 ID）；浏览器重连时按 `Last-Event-ID` 从库里补发漏掉的消息，所以 LISTEN 断开期间丢掉的通知不会造成漏显示。每 20 秒发一次心跳注释，响应头带 `Cache-Control: no-cache`。用 FastAPI 的 `fastapi.sse.EventSourceResponse`；构建时如果发现它不存在，就改用 `StreamingResponse(media_type="text/event-stream")`。
- **NOTIFY**：collector 在**同一个事务**里 INSERT 消息并执行 `pg_notify('new_message', ...)`，事务提交时通知才会发出。历史采集的批量写入不逐条 NOTIFY（怎么合并由第 10 项决定）。
- **advisory lock**：collector 用一条**专用长连接**持有会话级 advisory lock，这条连接一断就退出进程（连接池回收连接时会悄悄释放锁，所以不能从池里取）。空项目阶段用一个固定常量当锁的 key，按账号区分由第 6 项决定。不引入 pgbouncer。
- **collector 的致命错误**：token 失效或账号被封时**不退出、不重试登录**，而是把状态写进数据库，然后空转等待，直到 token 在 dashboard 里被更新。只有网络中断这类可以重试的错误才在进程内按退避节奏重连。这样 `restart: unless-stopped` 只负责真正的崩溃，不会变成反复登录的循环。细节由第 6 项决定。
- **Caddy**：`/api/*` 用 `reverse_proxy api:8000 { flush_interval -1 }`，让 SSE 不被缓冲；其余路径用 `try_files {path} {path}.html /index.html` 提供 `/srv` 下的静态文件。
- **Next.js 配置**：`output: 'export'`、`images.unoptimized: true`、`trailingSlash: false`。用到 `useSearchParams` 的组件要包在 `Suspense` 里，否则静态构建会报错。
- **前端类型生成**：`python -m harvester.api.export_openapi` 导出 `backend/openapi.json` 并提交；`pnpm gen:api` 从这个文件生成类型。CI 检查重新生成的结果和已提交的文件一致。
- **初始迁移**：只执行 `CREATE EXTENSION IF NOT EXISTS vector`；模型里暂时不引入 `Vector` 类型，等第 3 项和以后的 AI 功能再加。
- **空项目的最小验收**（对应 scope 第 1 项的完成标准）：`/api/health` 能查库并返回结果；一个演示用的 `/api/events` SSE 端点能推送心跳；collector 能拿到锁、连上库、写日志后空转（这时还没有 token，不连 Discord）；web 有一个页面能显示 health 的结果；`pnpm build` 和 `docker build` 都能通过。
- **版本**：镜像用 `python:3.13-slim`（构建时先确认 `discord.py-self` 在 3.13 上能装，装不上就退到 3.12）；Node 版本写进 `.nvmrc`，pnpm 版本写进 `packageManager`；其余依赖用当天的最新稳定版，由 `uv.lock` 和 `pnpm-lock.yaml` 锁住。
- **CI**：PR 上跑 `ruff check`、`ruff format --check`、`pyright`、OpenAPI 类型一致性检查、`pnpm build`、两个镜像的 `docker build`。

### 发布规则

- 镜像推到 GHCR 并设为私有，标签是 git sha，同时打 `latest`。服务器用一个只有 `read:packages` 权限的 PAT 执行一次 `docker login ghcr.io`。
- compose 用 `${IMAGE_TAG}` 引用镜像；回滚就是 `IMAGE_TAG=<旧 sha> ./deploy/deploy.sh`。
- 每次发布都更新 `caddy` 和 `api`。**只有改动碰到 `backend/src/harvester/collector/`、`backend/src/harvester/db/`、`backend/migrations/` 或后端依赖（`pyproject.toml`、`uv.lock`）时，才更新 `collector`**。CI 根据路径算出要更新哪些服务，传给 `deploy.sh`，脚本执行 `docker compose up -d <这些服务>`。有新迁移时，先跑 `migrate`。

### 我替你定下的细节

这些是不太值得你花时间权衡的实现细节。每条附上理由和备选，不同意可以直接改：

- **Python 包名 `harvester`**：短、描述用途。备选：`dcollector`。
- **路由**：Caddy 把 `/api/*` 转给 `api:8000`，其余路径返回静态文件（找不到时回退到 `index.html`）。备选：给 API 单独开子域名（会带来 CORS 和跨域 cookie 问题）。
- **静态导出的路由限制**：动态页面用查询参数（如 `/channels?id=123`），不用 `[id]` 动态段，因为静态导出要求构建时就知道所有路径。备选：`generateStaticParams`（频道是运行时数据，用不了）。
- **本地开发**：`compose.yml` 只起 `postgres`（端口只绑 `127.0.0.1:5432`）和 `caddy`（使用 `deploy/Caddyfile.dev`，在 `:8080` 上跑纯 HTTP）。api 和 collector 用 `uv run` 在本机跑，前端用 `pnpm dev`。本地 Caddy 通过 `host.docker.internal` 把 `/api/*` 转到本机 api，其余转到 Next 开发服务器（包括热更新用的 WebSocket），所以浏览器打开 `http://localhost:8080`，和线上一样是同源。备选：开发时在 FastAPI 打开 CORS（和线上行为不一致）。
- **NOTIFY 只带 ID**：频道 `new_message`，载荷只放 `message_id` 和 `channel_id`，api 收到后自己回库读完整的行。原因是 NOTIFY 载荷有 8000 字节上限，而且库才是唯一的数据来源。备选：载荷带全文（长消息会超限）。
- **collector 单实例**：启动时先拿 PostgreSQL advisory lock（按账号），拿不到就退出。原因是同一账号开两个会话既会重复写入，也会提高被封风险。备选：只靠 Compose 只开一个副本（部署出错时挡不住）。
- **时间**：数据库一律用 `timestamptz` 存 UTC，浏览器按本地时区显示。
- **初始迁移就执行 `CREATE EXTENSION vector`**：几乎零成本，以后加 AI 时不用碰线上库的扩展。
- **服务器初始化**：Ubuntu 24.04 LTS；绑定 Lightsail 静态 IP（挂在实例上不收费）；防火墙只开 22、80、443；SSH 只允许密钥登录；加 2 GB swap 防止内存尖峰杀掉 Postgres；镜像只构建 `linux/amd64`。
- **版本**：Next.js 和各依赖用构建当天的最新稳定版，并用 `uv.lock` 和 `pnpm-lock.yaml` 锁住。

### 不变量（必须一直成立）

- 进程之间只通过 PostgreSQL 交流：不互相调 HTTP，不共享文件。
- 只有 collector 连 Discord；api 永远不持有 Discord 会话。
- 任何时候每个账号最多一个 collector 在运行（advisory lock 保证，锁绑在专用连接上）。
- collector 绝不因为 token 失效或被封而反复登录。
- api 只有一个 worker。
- 浏览器只和一个域名打交道（同源），所以不配置 CORS。
- 账号 token、数据库密码、以后的交易所密钥只出现在 `.env` 和进程内存里，绝不进 git、日志或前端代码。
- 表结构只通过 Alembic 迁移修改。

### Configuration required

- `POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`：数据库账号。`.env` 里只存这三个，`DATABASE_URL`（`postgresql+asyncpg://...@postgres:5432/...`）由 compose 拼出来，避免两处配置不一致；本地开发时主机名是 `localhost`
- `TOKEN_ENCRYPTION_KEY`：加密保存 Discord token 的主密钥（具体算法由第 6 项 spec 决定）；空项目阶段可以不填
- `IMAGE_TAG`：要运行的镜像标签（git sha），由 `deploy.sh` 传入
- `SITE_DOMAIN`：Caddy 申请证书用的域名
- `LOG_LEVEL`：日志级别，默认 `INFO`
- GitHub Actions secrets：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`（发布用）；推送 GHCR 用内置的 `GITHUB_TOKEN`
- 服务器上一次性执行：用一个只读 PAT（`read:packages`）`docker login ghcr.io`，凭证保存在 `~/.docker/config.json`

## Consequences

**Positive**:
- 改 dashboard 或 API 只更新 `caddy` 和 `api`，小号不会频繁掉线重登，降低封号风险。
- 一个 PostgreSQL 撑起实时通知、任务队列和以后的向量检索，组件少，一台机器装得下。
- 以后加 AI 分析或下单，只是多一个入口和一个 Compose 服务，不用改现有结构。
- 前后端类型自动同步，接口改动不会悄悄弄坏页面。

**Negative / tradeoffs**:
- 前端是 TypeScript，后端是 Python，要维护两套工具链和两种语言的代码。
- Next.js 静态导出用不了服务端功能（Server Components 取数、中间件、动态路由段），所有数据都经 FastAPI 拿。
- PostgreSQL 和自建备份要你自己负责（第 9 项设计），没有托管数据库的自动备份。
- 单台服务器就是单点：机器出问题时采集会中断，只能靠历史采集（第 10 项）补齐。
- 机房 IP 登录用户账号本身就有封号风险，换厂商也避免不了。
- `.env` 放密钥对单人够用，但不适合长期放有资金权限的交易所密钥。
- 改动共用的 `db/` 模型或迁移时，collector 仍然会重启并重登一次；发布规则只能减少重登次数，不能完全避免。
- 发布规则靠路径判断要更新哪些服务，规则要随目录结构一起维护，判断漏了就会出现 collector 跑着旧代码的情况。

**Neutral**:
- 服务器在东京，而你在大阪，dashboard 有大约 10 毫秒的额外延迟。
- 第 2 项 `/audit` 会在搭好的空项目上把这些写进 `AGENTS.md`，并定 lint 规则。

## Follow-up

- [ ] 第 5 项「Dashboard 访问保护」：在本 spec 的同源加 cookie 约束下设计登录方式。
- [ ] 第 9 项「部署与常驻运行」：心跳、备份（`pg_dump` 加 Lightsail 快照）、服务器初始化脚本。
- [ ] 开 Lightsail 实例时在控制台看一下有没有大阪区域，有就选大阪。
- [ ] 第 9 项：GitHub Actions 的出口 IP 不固定，所以 22 端口只能对公网开放。配合只允许密钥登录和 fail2ban 来防护。
- [ ] 第 9 项：Lightsail 是突发型 CPU，长时间满载会被限速；历史采集和以后的 AI 分析要留意 CPU 配额。
- [ ] 加交易所下单之前，单独跑 `/architect` 设计：密钥改用权限更小的存储方式，交易所 API key 关闭提现权限并绑定 Lightsail 静态 IP 白名单，下单要有幂等键和审计记录。
- [ ] 加 AI 分析之前，单独跑 `/architect` 决定 LLM 提供商、embedding 模型和向量维度。
- [ ] 给 `AGENTS.md`（由 `/audit` 或 `/sync` 写）的备忘：`## Agent skills` 加上 `fastapi`、`vercel-react-best-practices`、`tanstack-query`、`postgresql-table-design`；`Declined:` 记上 Lightsail 部署 skill（`aws/agent-toolkit-for-aws`，它针对的是 Lightsail 自带的容器服务，不是 Compose）、其他未核实的社区 skill，以及全部 MCP 服务器（GitHub、Next.js DevTools；Postgres 没有可信的）。
