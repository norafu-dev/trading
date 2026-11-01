# Review, feat-dashboard-access-protection, 2026-11-01

**Reviewed by**: Sonnet 5.5（fresh model reviewer，作者在另一模型上）
**Scope**: 220 files（约 177 个源码文件），branch vs main
**Verdict**: Changes requested

## Summary
这个分支落地了 spec 0004 的 dashboard 访问保护（密码 + TOTP、会话、限速、Origin 校验、token 加密、前端 AuthGate 和设置/登录页），并带上了 spec 0002 的写入函数和 spec 0003 的前端基础。安全敏感部分整体写得扎实：登录流程拿咨询锁后顺序固定、会话 token 只存哈希、会话固定防护、TOTP 步进防重放、校验错误不回显输入、受保护路由默认挂 `protected_router`。主要问题是 CI 完全没有运行 pytest 和 Vitest，导致这批安全测试不在合并门槛里。其余是限速覆盖面和部署细节上的 Minor 问题。

## Major
### 🟠 CI 不跑 pytest 和 Vitest，安全测试不在合并门槛里, `.github/workflows/ci.yml:8-60`
**Problem**: ci.yml 的 backend job 只跑 `ruff check`、`ruff format --check`、`pyright`；web job 只跑 `pnpm lint`、`pnpm typecheck`、`pnpm build`。没有 postgres 服务，没有 `uv run pytest`，也没有 `pnpm test`（`web/package.json` 里有 `"test": "vitest run"`）。release.yml 通过 `workflow_call` 复用同一个 ci.yml，所以发布前也没有跑测试。AGENTS.md 的 Tooling 一节明确要求"在现有检查之外补上 biome check、pnpm typecheck、pytest 和 Vitest"。
**Why it matters**: 本分支新增的 `backend/tests/api/`（登录、设置、限速、路由保护、密钥泄漏）和 `backend/tests/db/` 以及前端 AuthGate、safe-next 等测试，目前只靠开发者本地手动运行。以后任何人改动 `PUBLIC_API_PATHS`、限速或 Origin 中间件造成回归，CI 和自动发布都会放行，直接部署到公网。
**Suggested fix**: 在 backend job 加 `pgvector/pgvector:pg18` 的 service container 和 `uv run pytest`；在 web job 加 `pnpm test`。

## Minor
### 🟡 `setup_complete` 的 TOTP 错误不计入失败，不受限速约束, `backend/src/harvester/api/auth_flows.py:176-178`
**Problem**: `step is None` 时直接 `return TotpInvalid()`，不调用 `record_event`；而 `FAILURE_OUTCOMES`（`auth_store.py:34`）只含 `bad_credentials` 和 `setup_bad_code`，所以这类失败既不进 `login_event`，也不推进退避。
**Why it matters**: 拿到有效设置码（1 小时内）的人可以不受限速地猜待确认的 6 位 TOTP（带 ±1 窗口，约每次 3/10^6 命中）。前提是已经持有 128 位的设置码，所以实际风险低，但与"所有失败都要限速"的流程注释不一致。
**Suggested fix**: 对 `TotpInvalid` 也记一条失败事件（可复用 `setup_bad_code` 或新增 outcome，注意 migration 里 `ck_login_event_outcome` 约束），或在文档里写明这是有意的取舍。

### 🟡 按 IPv6 单个地址限速，/64 内轮换地址可绕过 IP 限速, `backend/src/harvester/api/auth_core.py:48-56`、`backend/src/harvester/api/auth_store.py:62-78`
**Problem**: `request_context` 原样保存 `ipaddress` 合法的地址，`_ip_wait_seconds` 按完整 `ip` 精确匹配。IPv6 攻击者通常拥有整段 /64，每次换一个地址就是新的"IP"。
**Why it matters**: 单 IP 的指数退避对它不起作用，只剩全局 60 秒内 30 次的上限（`auth_core.py:34-35`），即约每天 4 万次尝试；同时每次失败都写一行 `login_event`，而清理 `purge_old_rows` 只在登录成功时才执行（`auth_flows.py:109`），攻击期间表会持续增长。有密码加 TOTP 两层，所以不是即时可利用的漏洞，但削弱了设计的限速意图。
**Suggested fix**: IPv6 按 /64 归一后再存和比较（可存归一后的网络地址用于限速）；清理逻辑放到不依赖登录成功的位置（比如限速分支或定时任务）。

### 🟡 开发入口信任任意 `X-Forwarded-For`，可伪造 IP 绕过按 IP 限速, `backend/src/harvester/api/__main__.py:19-26`
**Problem**: uvicorn 以 `host="0.0.0.0"`、`proxy_headers=True`、`forwarded_allow_ips="*"` 启动。生产里 api 容器没有发布端口（`deploy/compose.prod.yml` 只有 caddy 发布 80/443），只有 Caddy 能到达，Caddy 又用 `header_up X-Forwarded-For {remote_host}` 覆盖，所以生产是安全的。但本地开发时 api 直接监听在 :8000 且对局域网可达，任何人都可以直连并自带 XFF。
**Why it matters**: 本地或将来有人把 api 端口发布出去时，按 IP 限速可被随意绕过，只剩全局限速。当前依赖"只有 Caddy 可达"这一隐含前提，代码里没有表达。
**Suggested fix**: 开发时绑定 127.0.0.1（Caddy 用 host.docker.internal 的话需要另行处理），或把 `forwarded_allow_ips` 做成配置项，生产设成 Caddy 所在网段；至少在注释里写明这个前提。

### 🟡 未注册兜底异常处理器，未预期的错误不是统一 JSON 格式；`OSError` 被一律当成"数据库不可用", `backend/src/harvester/api/errors.py:90-94`
**Problem**: 只注册了 `ApiError`、`RequestValidationError`、`HTTPException` 和 `(OperationalError, InterfaceError, OSError)`。其他异常走 Starlette 默认的纯文本 500，不是 `{"error": {...}}`。另外任何 `OSError`（含文件、DNS 等无关错误）都会被报成 503 `database_unavailable`。
**Why it matters**: 违反 AGENTS.md "api 用一个统一的异常处理器转成固定格式的 JSON 错误"；前端 `toApiError` 虽然能容错，但运维排查时 503 会误导人去查数据库。
**Suggested fix**: 注册 `Exception` 的兜底处理器，返回固定格式的 500 `internal_error` 并记日志（不带异常内容）；把数据库类异常收窄到 SQLAlchemy 异常和 asyncpg 的连接错误。

### 🟡 发布计划不会因 `config.py` / `crypto.py` 变动重启 collector, `.github/workflows/release.yml:33-35`
**Problem**: 判断是否更新 collector 的正则只匹配 `src/harvester/(collector|db)/`、migrations、`pyproject.toml`、`uv.lock`。但 collector 启动时会调用 `get_crypto_settings()`（`collector/__main__.py:13`），而密文格式在 `harvester/crypto.py`，也是 collector 要用来解密 Discord token 的代码。
**Why it matters**: 以后只改 `crypto.py`（比如密文格式或密钥轮换）的发布会更新 api 而不更新 collector，二者对同一批库里密文的理解会不一致，直到下次碰巧重启 collector。
**Suggested fix**: 把 `src/harvester/(config|crypto|logging)\.py` 加进触发 collector 的路径。

### 🟡 生产 Caddyfile 缺少 HSTS 等基础安全头，且从未实际运行过, `deploy/Caddyfile:3-5`
**Problem**: 只设置了 `Content-Security-Policy: frame-ancestors 'none'` 和 `/api/auth/*` 的 `no-store`。没有 `Strict-Transport-Security`、`X-Content-Type-Options: nosniff`；`/api/login-events` 这类含 IP 和 UA 的受保护响应没有 `Cache-Control`。同时，已知生产 Caddyfile 没有被真正跑过（只测了 Caddyfile.dev），而它和 dev 版本在 `handle`、`try_files`、证书和 SITE_DOMAIN 上有差异。
**Why it matters**: `__Host-` cookie 加 Secure 已经要求 HTTPS，但没有 HSTS 时首次访问仍可能被降级到 http；未经运行的配置在首次部署时才暴露问题，而 deploy 又是自动的。
**Suggested fix**: 加 `Strict-Transport-Security`、`X-Content-Type-Options`，把 `Cache-Control: no-store` 扩到 `/api/*`；用 `caddy validate` 和本地 `docker compose -f compose.prod.yml` 配本地域名至少跑一次。

### 🟡 注释语言不符合项目约定, `backend/src/harvester/db/ingest.py:1-6`
**Problem**: AGENTS.md 要求代码注释一律简体中文，但 `db/ingest.py`、`db/snapshots.py`、`api/routes.py`（如 `"""Demo stream: ..."""`）、`deploy/Caddyfile`、`deploy/deploy.sh`、`deploy/compose.prod.yml` 的注释和 docstring 是英文。
**Why it matters**: 同一代码库两套风格；是显式写进规则的约定。
**Suggested fix**: 改动这些文件时顺手翻成中文（标识符和日志保持英文）。

## Nits
- ⚪ `backend/src/harvester/api/auth_routes.py:41-47` `LoginPassword` 没有长度上限，靠 Caddy 的 64KB 限制兜底；直连 api 时（开发）argon2 会在持锁状态下处理大密码。可加 `max_length` 防御。
- ⚪ `backend/src/harvester/api/auth_core.py:94-100` TOTP 接受 current+1 窗口并把 `totp_last_step` 推到未来一步后，用户下一个正常验证码（current 步）会被判为重复，最多等 30 秒。是已知取舍，建议在注释里点明。
- ⚪ `backend/src/harvester/api/auth_store.py:28-31` `AUTH_LOCK_KEY` 等常量之外，`SINGLE_ROW_ID` 与 migration 里 `id = 1` 约束是隐式对应，可在注释里指出。
- ⚪ `backend/src/harvester/db/ingest.py:161-185` 附件重新出现（先被标为 removed 后又在后续快照里带回）时 `removed_at` 不会被清空，属于边界情况，确认是否有意。

## Strengths
- 登录流程顺序固定且文档化：拿事务级咨询锁，再全局限速、IP 限速、校验、写事件，同一时刻最多一个 argon2 在算；只有密码和 TOTP 都对才写 `totp_last_step`，别人无法用错误密码烧掉你当前的验证码。
- 会话 token 只存 SHA-256、cookie 用 `__Host-` 前缀加 `HttpOnly` 加 `SameSite=Strict`，登录时删除旧会话防固定；SSE 每个心跳间隔都会重新检查会话，到期即断。
- 校验错误处理器只保留 `loc` 和 `type`，不回显密码和验证码；配置用 `hide_input_in_errors`；`parse_key` 的错误信息不带密钥内容；数据库不可用返回 503 而不是 401，前端不会误跳登录页。
- `protected_router` 默认带 `require_session`，公开路径是一个白名单常量，并有 `test_route_protection.py` 守着；前端 `safeNextPath` 同时挡住 `//`、反斜杠和控制字符。
- 数据模型写入函数用显式返回值（`Literal` 和 frozen dataclass）表达可预期结果，并发插入用 `ON CONFLICT DO NOTHING` 加行锁处理。

## Test coverage
配置了 pytest（真实 PostgreSQL）和 Vitest，覆盖面很好：后端有 auth_core 纯函数、登录、设置、限速、路由保护、密钥泄漏、迁移、索引、ingest 等测试；前端有 AuthGate、登录表单、设置向导、登录记录面板、safe-next、auth-expiry、errors 等测试。未覆盖的新逻辑：`setup_complete` 的 TOTP 错误是否计入限速（目前也没有计入）、IPv6 地址变化下的限速、`auth_reset.py` 和 `setup_code.py` 命令行入口。最大的缺口不在测试本身，而是 CI 没有运行它们（见 Major）。未提交的 `discord-markdown/` 改动有对应的新增失败路径测试（`use-markdown-parser.failure.test.ts`），行为一致。
