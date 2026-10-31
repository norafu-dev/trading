# 0004. Dashboard 访问保护

**Date**: 2026-10-31
**Status**: Proposed

## Summary

dashboard 放在公网上，里面有 Discord token 和全部采集数据，所以只有你能进。登录要同时输入密码和验证器 App 里的 6 位码（TOTP 二步验证）；登录状态存在数据库的会话表里，浏览器只拿到一个随机的 cookie，7 天不用或满 30 天就失效。第一次使用时，你先在服务器上跑一条命令拿到一次性设置码，再在网页上设置密码并扫码；验证器丢了，也是在服务器上跑一条命令重置。除了登录接口和一个只回答 ok 的存活检查，所有接口都要先登录。

## Requirements

**User stories**:
- 作为唯一的使用者，我想让任何不是我的人都打不开 dashboard 和接口，这样我的 Discord token 和采集数据不会泄露。
- 作为唯一的使用者，我想第一次部署后能在网页上完成设置，而且这段时间里别人没法抢先设置。
- 作为唯一的使用者，我想在经常用的设备上保持登录，不用每天输密码和验证码。
- 作为唯一的使用者，我想看到最近的登录记录，发现异常尝试。

**Acceptance criteria**:
- **AC-1**: 没有有效会话时，除了公开接口（`GET /api/auth/state`、`POST /api/auth/login`、`POST /api/auth/setup/begin`、`POST /api/auth/setup/complete`、`POST /api/auth/logout`、`GET /api/livez`）之外，所有 `/api/*` 接口都返回 401 和统一错误格式 `{"error": {"code": "unauthenticated", …}}`，其中包括 `/api/health` 和 SSE 的 `/api/events`。生产环境不提供 `/api/docs` 和 `/api/openapi.json`。
- **AC-2**: 新加的接口默认受保护：受保护的路由都挂在一个带 `require_session` 依赖的路由器上，公开接口只有一个写死的白名单（开发环境的 `/api/docs`、`/api/openapi.json` 也在白名单里）。两个测试守住它：一个遍历 `app.routes`（包括 WebSocket 路由和 mount），发现不在白名单里又没有 `require_session` 的 `/api/*` 路由就失败；另一个黑盒测试不带 cookie 请求每一个路由，断言结果是 401，或者这个路由在白名单里。
- **AC-3**: 还没设置过（库里没有 `dashboard_credential`）时，登录接口返回 409 `setup_required`；在服务器上运行 `docker compose exec api python -m harvester.api.setup_code`（本地用 `uv run python -m harvester.api.setup_code`，两者都需要能连上数据库）会生成一个 1 小时内有效的一次性设置码（`secrets.token_urlsafe(16)`，128 位，从终端复制粘贴），只打印到运行命令的终端，库里只存它的 SHA-256，日志里不出现。已经设置过时，这个命令拒绝执行并提示先重置。没有有效设置码时，设置接口一律返回 403 `setup_code_invalid`。设置码在 `complete` 成功之前可以重复使用。
- **AC-4**: 设置页分四步：输入设置码 → 设密码（先做 NFKC 规范化，再按 Unicode 码点数算，12 到 128 个，输入两次）→ 显示二维码和可手动输入的密钥 → 输入一次验证码确认。只有验证码正确时才保存凭据；保存后设置码作废，你直接处于登录状态，并跳到 dashboard 首页。中途离开或验证码输错，什么都不保存，可以重新开始（再次开始会生成新的 TOTP 密钥）。
- **AC-5**: 登录页是一个表单，密码和 6 位验证码一起提交。两者都对才登录成功；任一不对都只提示「密码或验证码不对」，不区分是哪一个。验证码允许前后各差一个时间步（手机时间略有偏差也能登录），取实际匹配到的那一步；这一步必须大于 `totp_last_step`，所以同一个验证码用过之后不能再用。只有密码和验证码都对时才更新 `totp_last_step`（密码错时不更新，别人没法用错误密码把你当前的验证码烧掉）。
- **AC-6**: 登录成功后设置会话 cookie：生产环境名字是 `__Host-harvester_session`，带 `Secure`、`HttpOnly`、`SameSite=Strict`、`Path=/`，不带 `Domain`；本地开发（`SESSION_COOKIE_SECURE=false`）名字是 `harvester_session`，不带 `Secure`。`Max-Age` 是 2592000（30 天，等于会话的绝对有效期），关掉浏览器也保持登录。cookie 值是 32 字节随机数，库里只存它的 SHA-256。登录或设置完成时，如果请求里带着旧的会话 cookie，先删掉那一行再建新的（防会话固定）。
- **AC-7**: 会话在最后一次使用 7 天后失效，并且无论是否在用，创建 30 天后一定失效。失效的会话访问受保护接口返回 401。`last_seen_at` 最多每 60 秒写一次库。
- **AC-8**: 登出删除当前会话那一行并清掉 cookie；之后同一个 cookie 再访问返回 401。登出接口即使没有会话也返回 204。
- **AC-9**: 一个 IP 的「连续失败次数」是：这个 IP 最近 24 小时内、在它最后一次成功之后的 `bad_credentials` 和 `setup_bad_code` 条数（`rate_limited` 既不计数也不打断；别的 IP 成功不影响这个 IP）。连续失败 5 次后，要从最后一次失败起等 1 分钟才能再试，之后每多失败一次等待时间翻倍（2、4、8 分钟……），最长 1 小时；等待期间请求返回 429 `rate_limited`，带 `Retry-After` 头和 `retry_after_seconds`，而且不检查密码。所有 IP 加起来最近 60 秒内的失败超过 30 次时，新尝试一律返回 429（成功登录不计入）。没有任何情况会把账号永久锁死。设置码输错和登录失败一起计数。并发的登录和设置请求不能一起穿过门槛：它们在一把全局的事务级 advisory lock 下串行执行（见 Key invariants）。
- **AC-10**: 所有会改数据的请求（`POST`、`PUT`、`PATCH`、`DELETE`）都检查 `Origin` 头必须和 `SITE_ORIGIN` 完全相同（`SITE_ORIGIN` 在配置校验时规范化：小写、去掉末尾的 `/`、去掉默认端口），缺少、`null` 或不一致返回 403 `bad_origin`。包括登录、设置和登出。这个检查在限速和解析请求体之前进行，`bad_origin` 不写 `login_event`。
- **AC-11**: 每次登录成功、失败、被限速、设置码错误、设置完成、登出和重置都记一条 `login_event`（时间、IP、浏览器标识、结果）；`rate_limited` 同一个 IP 每 60 秒最多记一条，免得被刷满。服务器命令产生的记录 `ip` 为空。超过 180 天的记录在每次登录成功时删除，过期会话也在那时删除（很久不登录时它们会留在表里，不影响安全）。dashboard 的「安全」页显示最近 50 条，最新的在上面，列出时间、IP（命令产生的显示「服务器命令」）、浏览器标识（超长的截断显示，悬停看全文）和结果。
- **AC-12**: 在服务器上运行 `python -m harvester.api.auth_reset`（需要确认输入 `RESET`）会删除凭据、所有会话和设置码，并记一条 `reset`；之后 dashboard 回到「未设置」状态，按 AC-3 重新设置。
- **AC-13**: 没登录时打开 dashboard 的任何页面，先显示全屏加载（不出现侧栏和任何数据），确认状态后：未设置跳到 `/setup`，未登录跳到 `/login?next=<原地址>`；连不上服务器（网络错误或 503）时显示「无法连接服务器」和「重试」，不跳转。登录成功后回到 `next`；`next` 只接受以 `/` 开头、不以 `//` 开头的站内路径，否则回首页。已登录时打开 `/login` 或 `/setup` 直接去首页。
- **AC-14**: 使用中会话失效时，任何接口返回 401 都会让前端跳到 `/login?next=<当前地址>&reason=expired`，登录页显示「登录已过期，请重新登录」。SSE 连接不管有没有事件，按时间每 20 秒重新检查一次会话（每次检查单独开一个短的数据库会话，流期间不一直占着连接），失效就断开，前端随之检查登录状态并跳转。所以登出或重置后，其他设备最多再收到 20 秒的数据。
- **AC-15**: 图标栏底部有「安全」入口（打开 `/settings/security`）和「登出」按钮。
- **AC-16**: `TOKEN_ENCRYPTION_KEY` 在 api 和 collector 启动时必须存在并且格式正确，否则启动报错退出（`migrate` 不校验它）。TOTP 密钥用 AES-256-GCM 加密后存库，密文带密钥版本号，用途名 `totp` 作为附加数据；直接把库里的密文当成别的用途（例如 `discord_token`）解密会失败。
- **AC-17**: api 启动时校验：`SESSION_COOKIE_SECURE=false` 但 `SITE_ORIGIN` 解析出的 hostname 不是 `localhost` 或 `127.0.0.1` 时，报错退出（`http://localhost.evil.com` 也会被拒绝）。日志和接口响应里永远不出现密码、验证码、设置码、TOTP 密钥、cookie 值；特别是 422 校验错误不回显输入值。
- **AC-18**: Caddy 对 `/api/*` 的请求体限制在 64KB 以内；`/api/auth/*` 的响应带 `Cache-Control: no-store`；所有页面带 `Content-Security-Policy: frame-ancestors 'none'`（防止 dashboard 被别的网站嵌进框里做点击劫持）；Caddy 转发给 api 时显式设置 `header_up X-Forwarded-For {remote_host}`。

## Decision

**Chosen option**: Option 1: 应用内自己实现单用户的「密码 + TOTP」登录，会话存在 PostgreSQL，cookie 只带随机 ID

在 FastAPI 里用成熟的密码哈希库（`argon2-cffi`）和 TOTP 库（`pyotp`）加一张会话表实现登录，不引入外部认证服务；前端在静态导出的约束下用一个登录状态接口加客户端跳转来守门。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `shadcn` (`shadcn-ui/ui`, `.claude/skills/shadcn/`)

## Rationale

理由和其他选项见 [rationale.md](rationale.md)。

## Feature design

**Data model sketch**（一个 Alembic 迁移，四张表互相独立，没有外键）:

**`dashboard_credential`**（最多一行）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | smallint | 是 | 主键，`CHECK (id = 1)` |
| `password_hash` | text | 是 | argon2id 的 PHC 字符串 |
| `totp_secret_enc` | bytea | 是 | AES-256-GCM 密文，用途名 `totp` |
| `totp_last_step` | bigint | 否 | 最近一次成功使用的时间步，用来拒绝重复使用的验证码 |
| `created_at`、`updated_at` | timestamptz | 是 | `server_default now()` |

**`dashboard_session`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `token_hash` | bytea | 是 | 主键，cookie 值的 SHA-256（32 字节，`CHECK (octet_length(token_hash) = 32)`） |
| `created_at` | timestamptz | 是 | `server_default now()` |
| `last_seen_at` | timestamptz | 是 | `server_default now()` |
| `ip` | inet | 是 | 登录时的 IP |
| `user_agent` | text | 否 | 截断到 512 个字符 |

索引：`created_at`。有效条件：`last_seen_at > now() - interval '7 days' AND created_at > now() - interval '30 days'`。

**`login_event`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint identity | 是 | 主键 |
| `occurred_at` | timestamptz | 是 | `server_default now()` |
| `ip` | inet | 否 | 服务器命令产生的记录（`reset`）为空；网页请求必有 |
| `user_agent` | text | 否 | 截断到 512 个字符 |
| `outcome` | text | 是 | `CHECK (outcome IN ('success', 'bad_credentials', 'rate_limited', 'setup_bad_code', 'setup_completed', 'logout', 'reset'))` |

索引：(`ip`, `occurred_at` DESC)、(`occurred_at` DESC)。

**`dashboard_setup_code`**（最多一行）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | smallint | 是 | 主键，`CHECK (id = 1)` |
| `code_hash` | bytea | 是 | 设置码的 SHA-256 |
| `pending_totp_secret_enc` | bytea | 否 | 设置过程中生成、还没确认的 TOTP 密钥（加密，用途名 `totp`） |
| `expires_at` | timestamptz | 是 | 生成后 1 小时 |
| `created_at` | timestamptz | 是 | `server_default now()` |

**登录状态**（由数据计算，不单独存）:
`setup_required`（没有凭据）→ `unauthenticated`（有凭据，请求没有有效会话）⇄ `authenticated`（有有效会话）。`auth_reset` 让任何状态回到 `setup_required`。

**API surface**（全部在 `/api` 下，错误都是统一格式 `{"error": {"code": str, "message": str, "retry_after_seconds"?: int}}`）:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/auth/state` | GET | 无 | `{"state": "setup_required" \| "unauthenticated" \| "authenticated"}` | 公开 | 无（总是 200） |
| `/auth/setup/begin` | POST | `setup_code: str` | `{"otpauth_uri": str, "secret": str}`（每次调用都生成新密钥并覆盖 `pending_totp_secret_enc`） | 公开 + Origin | 403 `setup_code_invalid`、403 `bad_origin`、409 `already_set_up`、429 `rate_limited` |
| `/auth/setup/complete` | POST | `setup_code: str`、`password: str`（12 到 128）、`totp_code: str`（6 位数字） | 204 + Set-Cookie | 公开 + Origin | 403 `setup_code_invalid`、400 `totp_invalid`、422 校验失败、409 `already_set_up`、429 |
| `/auth/login` | POST | `password: str`、`totp_code: str` | 204 + Set-Cookie | 公开 + Origin | 401 `bad_credentials`、409 `setup_required`、429 `rate_limited`、403 `bad_origin` |
| `/auth/logout` | POST | cookie | 204 + 清除 cookie | 公开 + Origin | 403 `bad_origin` |
| `/auth/login-events` | GET | `limit: int`（默认 50，1 到 200，超出返回 422） | `[{"occurred_at", "ip", "user_agent", "outcome"}]`，按时间倒序 | 需要会话 | 401 |
| `/livez` | GET | 无 | `{"status": "ok"}`，不查库 | 公开 | 无 |
| `/health`、`/events` 和以后所有接口 | 原样 | 原样 | 原样 | 需要会话 | 401 |

`/auth/setup/begin` 用一条原子语句校验并写入：`UPDATE dashboard_setup_code SET pending_totp_secret_enc = :enc WHERE id = 1 AND code_hash = :h AND expires_at > now()`，影响 0 行就是 403 `setup_code_invalid`（并发的两个 `begin` 后到的覆盖先到的，只有最后显示的二维码有效）。`/auth/setup/complete` 同样先按 `code_hash` 和 `expires_at` 取行，再用 `pending_totp_secret_enc` 校验验证码；成功后在一个事务里写 `dashboard_credential`（同时把这次的时间步写进 `totp_last_step`）、删除 `dashboard_setup_code`、创建会话、记 `setup_completed`。

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| 所有受保护请求 | 当前会话 | cookie 值做 SHA-256 后查 `dashboard_session.token_hash`，再按有效条件过滤 |
| 所有受保护请求 | 要不要更新 `last_seen_at` | `now() - last_seen_at >= 60 秒` |
| 登录、设置 | 客户端 IP | `request.client.host`；uvicorn 已开 `proxy_headers=True`、`forwarded_allow_ips="*"`，这时它取 `X-Forwarded-For` 最左边的值。这个值可信依赖三个前提，都要保持：生产环境 api 不对外开端口（`compose.prod.yml` 里 api 没有 `ports`），Docker 网络里只有我们自己的容器；Caddy 显式设置 `header_up X-Forwarded-For {remote_host}`，只传一个真实客户端 IP（AC-18）。本地开发时这条链不成立，按 IP 限速在本地不可靠，可以接受 |
| 登录、设置 | 浏览器标识 | `User-Agent` 头，截断到 512 |
| 限速 | 该 IP 连续失败次数和最后失败时间 | `login_event`：`WHERE ip = :ip AND outcome IN ('bad_credentials','setup_bad_code') AND occurred_at > greatest(now() - interval '24 hours', coalesce((SELECT max(occurred_at) FROM login_event WHERE ip = :ip AND outcome = 'success'), '-infinity'))` 的条数和最大 `occurred_at` |
| 限速 | 需要等待的秒数 | 纯函数 `backoff_seconds(failures) = 0 if failures < 5 else min(60 * 2 ** (failures - 5), 3600)`，剩余等待 = 最后失败时间 + 等待秒数 − 现在 |
| 限速 | 全局失败次数 | `login_event` 最近 60 秒内 `bad_credentials` 和 `setup_bad_code` 的条数，超过 30 就拒绝 |
| 限速 | 要不要写 `rate_limited` 记录 | 这个 IP 最近 60 秒内没有 `rate_limited` 记录时才写 |
| 登录 | TOTP 时间步 | `floor(unix 时间 / 30)`，时间从注入的时钟函数来；依次试 当前步、前一步、后一步，取第一个验证码匹配的步；它必须大于 `totp_last_step` |
| 设置 | 密码长度 | `unicodedata.normalize("NFKC", password)` 之后的 `len()`（码点数），在 Pydantic 校验器里做，哈希的也是规范化后的值 |
| 设置 | `otpauth_uri` | `pyotp.TOTP(secret).provisioning_uri(name="dashboard", issuer_name="Harvester")` |
| 设置页 | 二维码 | 前端用 `qrcode.react` 的 `QRCodeSVG` 把 `otpauth_uri` 画出来（不用后端 SVG，因为那要插入 HTML） |
| 会话 cookie | 名字、`Secure` 标志 | `SESSION_COOKIE_SECURE` |
| 会话 cookie | `Max-Age` | 固定 2592000 |
| Origin 检查 | 允许的来源 | `SITE_ORIGIN`（规范化后） |
| 加密 | 当前密钥和版本号 | `TOKEN_ENCRYPTION_KEY`，格式 `<版本号>:<标准 base64>`，版本号是 1 到 255 的十进制整数，base64 严格解码（带 padding）后必须正好 32 字节，例如 `1:…` |
| 解密旧密文 | 旧密钥 | `TOKEN_ENCRYPTION_OLD_KEYS`（可选，逗号分隔的同格式列表）；所有版本号（含当前）不能重复，否则配置校验失败 |
| 安全页 | 结果的中文名、IP 显示 | 前端映射表；`ip` 为空显示「服务器命令」 |
| 登录页提示 | 「登录已过期」 | 查询参数 `reason=expired` |
| 安全页 | 登录记录 | `GET /api/auth/login-events` |

**加密格式**（`harvester/crypto.py`，api 和 collector 共用，第 6 项保存 Discord token 也用它）:
密文 = `版本号（1 字节）` + `nonce（12 字节随机）` + `AES-256-GCM 密文和 tag`；附加数据是用途名的 UTF-8 字节（`b"totp"`、以后的 `b"discord_token"`）。接口是纯函数：`encrypt(keys, purpose, plaintext) -> bytes`、`decrypt(keys, purpose, blob) -> bytes | DecryptError`，`keys` 从配置传入。`python -m harvester.crypto genkey` 打印一个新的 `1:<base64>`。

**Key invariants**:
- 受保护是默认值：任何不在公开白名单里的 `/api/*` 路由都必须经过 `require_session`（由 AC-2 的测试保证）。
- 库里永远不存明文的密码、cookie 值、设置码或 TOTP 密钥。
- `dashboard_credential` 和 `dashboard_setup_code` 都最多一行（`CHECK (id = 1)`）。
- 已有凭据时，`setup/begin` 和 `setup/complete` 一律拒绝，不管设置码是否还在。
- 限速只从 `login_event` 计算，api 进程里不保存任何限速状态，重启不会清零。
- 被限速的请求不做密码校验（防止借限速窗口继续试密码，也省 CPU）。
- 登录、`setup/begin`、`setup/complete` 的处理顺序固定：Origin 检查 → 请求体校验 → 开事务并拿 `pg_advisory_xact_lock(AUTH_LOCK_KEY)`（一个固定常量，和 collector 的锁 key 不同）→ 全局限速 → 该 IP 限速 → 校验（argon2 在 `asyncio.to_thread` 里跑，不阻塞事件循环）→ 写结果和 `login_event` → 提交。拿锁让并发请求一个一个来，不会一起穿过限速门槛，也保证同一时间最多只有一个 argon2 在算，防止用并发请求把 CPU 打满。
- 只有密码和验证码都对时才写 `totp_last_step`。
- 时间（`now`）和随机数生成都从参数注入，校验逻辑（`backoff_seconds`、`verify_totp_step`、`session_is_valid`、`safe_next_path`）是纯函数。

**Security model**:
- 只有一个用户，没有角色。持有有效会话 cookie 的请求拥有全部权限。
- 密码用 `argon2-cffi` 的 `PasswordHasher` 默认参数（argon2id）；每次登录成功后，如果参数已经过时（`check_needs_rehash`），就重新哈希。
- 密码校验和 TOTP 校验总是都做完再返回，统一回答 `bad_credentials`，不透露哪个错了。
- CSRF 防护：cookie 是 `SameSite=Strict`，加上 AC-10 的 Origin 检查。`GET` 接口不改数据（项目约定）。
- 会话 cookie 是 `HttpOnly`，前端 JS 读不到；前端不存任何令牌。
- 生产环境关闭 `/api/docs` 和 `/api/openapi.json`（`create_app(settings)` 按 `SITE_ORIGIN` 是否为 localhost 决定）；`export_openapi` 用一组固定的本地默认配置调用 `create_app(settings)` 再取 `app.openapi()`，不需要真实的 `.env`。
- 统一错误处理器转换 422 时，每个字段错误只输出 `loc`（字段路径）和 `type`（错误类型），去掉 FastAPI 默认带的 `input` 和 `ctx`，所以密码和验证码不会出现在响应里。
- 外部监控只能用 `/api/livez`，它不查库，所以数据库挂了外部看不出来；数据库状态要登录后看 `/api/health`。这是有意的取舍。
- 能 SSH 上服务器的人可以生成设置码和重置，这是有意的：服务器本身就是信任边界。
- 本地开发时 api 监听在宿主机的 `0.0.0.0:8000`，同一局域网里的设备可以伪造 `X-Forwarded-For` 绕过按 IP 限速；这只影响本地，可以接受。

**Configuration required**:
- `SITE_ORIGIN`：允许的来源，生产是 `https://<SITE_DOMAIN>`，本地是 `http://localhost:8080`。api 必填。`compose.prod.yml` 里写成 `SITE_ORIGIN: https://${SITE_DOMAIN}`，不在 `.env` 里重复一份。
- `SESSION_COOKIE_SECURE`：默认 `true`；本地 `.env` 设 `false`。
- `TOKEN_ENCRYPTION_KEY`：从可选改成 api 和 collector 必填，格式见 Value sourcing，用 `python -m harvester.crypto genkey` 生成。`compose.prod.yml` 改成 `${TOKEN_ENCRYPTION_KEY:?set in .env}`，缺了就拒绝启动整套服务。
- `TOKEN_ENCRYPTION_OLD_KEYS`：可选，换密钥时放旧密钥。
- 配置拆分：`harvester.config` 保留共用的 `Settings`（数据库、日志）；新增 `CryptoSettings`（`TOKEN_ENCRYPTION_KEY`、`TOKEN_ENCRYPTION_OLD_KEYS`，api 和 collector 启动时校验）和 `ApiSettings`（`SITE_ORIGIN`、`SESSION_COOKIE_SECURE`，只有 api 校验）。`migrate` 只用 `Settings`。各自的 `get_*_settings()` 带 `lru_cache`，测试里清缓存或直接构造实例传入。
- `.env.example` 同步更新这四项和生成方法。
- 部署顺序：先在服务器 `.env` 里加上 `TOKEN_ENCRYPTION_KEY`，再部署这一版，避免 api 起来了但 collector 因缺密钥卡住。

**前端**:
- 新页面放在 `(dashboard)` 路由组外面：`web/src/app/login/page.tsx`、`web/src/app/setup/page.tsx`，不带侧栏，居中卡片布局（spec 0003 的 token 和组件）。
- `web/src/components/auth/AuthGate.tsx`：在 `(dashboard)/layout.tsx` 里包住 `AppShell`；用 TanStack Query 查 `/api/auth/state`（`queryKey: ["auth", "state"]`，`staleTime: 0`，失败时自动重试 2 次），加载中显示全屏 `Spinner`，`setup_required` 跳 `/setup`，`unauthenticated` 跳 `/login?next=…`，`authenticated` 才渲染外壳；重试后仍然网络错误或 503 时显示全屏「无法连接服务器」和「重试」按钮（AC-13）。
- `web/src/lib/api/client.ts` 加 openapi-fetch 中间件：除了 `/api/auth/*` 之外的任何 401 响应，都 `window.location.assign("/login?next=…&reason=expired")`（AC-14）。`toUiError` 解析统一错误格式里的 `error.code`。
- `safe_next_path`（前端 `lib/auth/safe-next.ts`）：只接受以 `/` 开头、不以 `//` 开头、不含 `\` 的路径。
- SSE：`EventSource` 出错时让 `["auth","state"]` 查询失效，由 `AuthGate` 处理跳转。
- 安全页 `web/src/app/(dashboard)/settings/security/page.tsx`：表格显示最近 50 条登录记录（时间按 spec 0003 的时间规则，结果用中文：成功、密码或验证码错误、被限速、设置码错误、完成设置、登出、重置）。
- `ServerRail` 底部加「安全」（`Shield` 图标）和「登出」（`LogOut` 图标）。登出成功后跳 `/login`。

**统一错误格式**（本项先用到，项目通用）:
`backend/src/harvester/api/errors.py` 定义 `ApiError(status, code, message, retry_after_seconds=None)` 和子类，`create_app` 注册一个异常处理器，把它转成 `{"error": {"code", "message", "retry_after_seconds"?}}`，429 时同时设置 `Retry-After` 头。FastAPI 自带的 422 校验错误也由这个处理器转成 `{"error": {"code": "validation_error", "message": …, "fields": [...]}}`。这也是 spec 0003 Follow-up 里 `toUiError` 等的格式。

**边界情况**:
- 两个标签页同时完成设置：`dashboard_credential` 的 `CHECK (id = 1)` 加主键冲突让后到的失败，返回 409 `already_set_up`。
- 两个请求同时用同一个验证码登录：更新 `totp_last_step` 用 `UPDATE … SET totp_last_step = :step WHERE id = 1 AND (totp_last_step IS NULL OR totp_last_step < :step)`，影响 0 行就当作 `bad_credentials`。
- 设置码过期：`setup/begin` 和 `setup/complete` 返回 403 `setup_code_invalid`，设置页提示在服务器上重新运行命令。
- 重新运行 `setup_code` 会覆盖旧的设置码（以及未确认的 TOTP 密钥）。
- `TOKEN_ENCRYPTION_KEY` 换了但没把旧密钥放进 `TOKEN_ENCRYPTION_OLD_KEYS`：TOTP 密钥解不开，登录返回 500 并在日志里记 `totp_secret_undecryptable`（不含密钥内容）；恢复办法是改回密钥，或者 `auth_reset` 后重新设置。
- 数据库不可用：受保护接口返回 503 `database_unavailable`（不是 401，避免前端误跳登录页）；`/api/livez` 仍然返回 ok。

**Observability**: `structlog` 记录 `auth.login_succeeded`、`auth.login_failed`、`auth.rate_limited`、`auth.setup_completed`、`auth.logout`、`auth.reset`，字段只有 `ip` 和结果，不记任何密钥或输入值。

**Critical test scenarios**:
- 集成测试：遍历 `app.routes`，白名单之外的 `/api/*` 路由都依赖 `require_session`；无 cookie 访问 `/api/health` 返回 401 和统一错误格式，verifies **AC-1**、**AC-2**
- 集成测试：没有凭据时登录返回 409；运行 `setup_code` 后，用错误设置码返回 403 并记 `setup_bad_code`；正确设置码 `begin` 返回 URI；用错误验证码 `complete` 不保存任何东西；正确验证码保存凭据、删除设置码、返回会话 cookie；再次 `begin` 返回 409，verifies **AC-3**、**AC-4**
- 集成测试：设置码过期后返回 403；已设置时 `setup_code` 命令拒绝执行，verifies **AC-3**
- 集成测试：密码对验证码错、密码错验证码对，都返回同样的 401 `bad_credentials`；同一时间步的验证码第二次使用被拒绝；前后一个时间步的验证码能用，verifies **AC-5**
- 集成测试：cookie 属性在 `SESSION_COOKIE_SECURE` 为真和假两种配置下符合 AC-6；库里存的是哈希不是 cookie 值，verifies **AC-6**
- 集成测试（注入时钟）：7 天不用失效、30 天到期失效、60 秒内多次请求只写一次 `last_seen_at`，verifies **AC-7**
- 集成测试：登出后同一个 cookie 返回 401；没有 cookie 登出返回 204，verifies **AC-8**
- 单元测试：`backoff_seconds` 在 4、5、6、11、12、100 次失败时的值；集成测试：同一 IP 第 6 次尝试在等待期内返回 429 且不校验密码；成功登录后计数清零；25 小时前的失败不计数；另一个 IP 成功不影响这个 IP；全局 60 秒内 30 次失败后下一次返回 429，成功登录不计入；同时发出 10 个错误密码的登录请求，最终 `bad_credentials` 正好 5 条、其余是 429，verifies **AC-9**
- 集成测试：被限速期间连续请求 20 次，`rate_limited` 记录只有 1 条；`bad_origin` 不写记录，verifies **AC-10**、**AC-11**
- 集成测试：密码错但验证码对时，`totp_last_step` 不变，随后用同一个验证码加正确密码仍能登录；带旧 cookie 登录后旧会话行被删除，verifies **AC-5**、**AC-6**
- 集成测试：提交一个过短的密码，422 响应里搜不到提交的密码；`limit=500` 返回 422，verifies **AC-11**、**AC-17**
- 集成测试：`POST /api/auth/login` 缺 `Origin` 或 `Origin` 不一致返回 403 `bad_origin`，verifies **AC-10**
- 集成测试：各种结果都写进 `login_event`；登录成功时清理 180 天前的记录和过期会话；`login-events` 按时间倒序且受保护，verifies **AC-11**
- 集成测试：`auth_reset` 后状态变回 `setup_required`，旧会话失效，verifies **AC-12**
- 单元测试：`safe_next_path` 拒绝 `//evil.com`、`https://evil.com`、`/\evil.com`，接受 `/channels?id=1`，verifies **AC-13**
- 前端渲染测试：`AuthGate` 三种状态分别显示加载、跳设置页、跳登录页带 `next`；客户端中间件遇到 401 跳转并带 `reason=expired`，verifies **AC-13**、**AC-14**
- 集成测试：SSE 连接在会话被删除后的下一次心跳断开，verifies **AC-14**
- 单元测试：`encrypt` 后 `decrypt` 还原；用途名不同解密失败；旧版本号的密文能用 `TOKEN_ENCRYPTION_OLD_KEYS` 解开；格式错误的 `TOKEN_ENCRYPTION_KEY` 让配置校验失败，verifies **AC-16**
- 单元测试：`SESSION_COOKIE_SECURE=false` 加非 localhost 的 `SITE_ORIGIN`（包括 `http://localhost.evil.com`）让配置校验失败；`SITE_ORIGIN` 规范化去掉末尾 `/` 和默认端口；版本号重复或 base64 长度不对的密钥让配置校验失败；集成测试：一次完整的设置和登录之后，日志输出里搜不到密码、验证码、设置码和 cookie 值，verifies **AC-17**
- 前端渲染测试：`AuthGate` 在网络错误时显示「无法连接服务器」且不跳转，verifies **AC-13**
- 手动验证：没登录打开首页不闪现侧栏；图标栏底部的安全页和登出能用；用 `curl -I` 检查线上 `/api/auth/state` 带 `Cache-Control: no-store`、页面带 `frame-ancestors 'none'`，超过 64KB 的请求体被拒绝，verifies **AC-13**、**AC-15**、**AC-18**

## Build plan

按 Tracer Bullet：先打通「设置码 → 设置 → 登录 → 受保护接口 → 前端守门」这条最细的线，确认 cookie、Origin 和静态导出下的跳转都能工作，再加限速、记录、过期这些加粗的部分。

1. 配置和基础设施：拆出 `CryptoSettings` 和 `ApiSettings` 并写好校验（`SITE_ORIGIN` 规范化和 localhost 判断、密钥格式和版本号唯一）；`harvester/crypto.py` 和 `genkey`；`api/errors.py` 统一错误格式和处理器（422 不回显输入）；`create_app(settings)` 和 `export_openapi` 的默认配置；更新 `.env.example`、`compose.prod.yml`（`SITE_ORIGIN` 由 `SITE_DOMAIN` 拼出，密钥 `:?` 必填）、`Caddyfile`（请求体上限、`no-store`、`frame-ancestors`、`header_up X-Forwarded-For`）。satisfies **AC-1**、**AC-16**、**AC-17**、**AC-18**
2. 一个 Alembic 迁移建四张表。satisfies **AC-3**、**AC-6**、**AC-11**
3. 细线后端：`setup_code` 命令、`/auth/state`、`/auth/setup/begin`、`/auth/setup/complete`、`/auth/login`、`/auth/logout`、`require_session`、Origin 检查中间件；把现有路由挂到受保护路由器上，加 `/api/livez`，生产关闭文档路由；路由白名单测试。satisfies **AC-1**、**AC-2**、**AC-3**、**AC-4**、**AC-5**、**AC-6**、**AC-8**、**AC-10**
4. 细线前端：`/setup` 四步页（`qrcode.react`）、`/login` 页、`AuthGate`、api 客户端的 401 中间件、`safe-next`；`pnpm gen:api` 同步类型；本地经 Caddy `:8080` 走通设置、登录、刷新、登出。satisfies **AC-4**、**AC-5**、**AC-13**、**AC-14**
5. 加粗：会话 7 天和 30 天过期、`last_seen_at` 节流、TOTP 时间步防重复（只在两者都对时写）、advisory lock 串行化和 argon2 放进线程、限速（`backoff_seconds`、全局 30 次失败、`rate_limited` 去重）、`login_event` 全部写入和 180 天清理、登录时删除旧会话。satisfies **AC-5**、**AC-6**、**AC-7**、**AC-9**、**AC-11**
6. SSE 心跳时检查会话；`auth_reset` 命令；`/auth/login-events`、安全页、图标栏的安全和登出入口。satisfies **AC-11**、**AC-12**、**AC-14**、**AC-15**
7. 测试：按 Critical test scenarios 写后端集成测试（真实 PostgreSQL）和单元测试、前端渲染测试；检查日志和响应里没有密钥。satisfies **AC-1** 到 **AC-18**

## Consequences

**Positive**:
- 不依赖任何外部认证服务，部署方式不变，本地和线上行为一致。
- 二步验证让单独泄露的密码没用；会话存库，`auth_reset` 能立刻让所有设备失效。
- 限速状态从登录记录计算，api 重启不清零，也不需要 Redis 之类的新组件。
- 受保护是默认值，以后加接口忘了写认证也会被测试拦下。
- 统一错误格式和加密格式在这里定好，第 6 项和以后的接口直接用。

**Negative / tradeoffs**:
- 认证代码是自己写的（虽然密码哈希和 TOTP 用的是成熟库），会话、限速、Origin 检查这些都要自己测全，出错就是安全问题。
- 每个受保护请求多一次数据库查询（单人使用，影响可以忽略）。
- 没有会话列表，无法单独踢掉某一台设备，只能 `auth_reset` 全部踢掉；网页上也不能改密码。
- 验证器丢了必须 SSH 上服务器才能恢复，没有备用码。
- 静态导出没法在服务端拦截页面，未登录的人能下载到前端的静态文件（不含任何数据）。
- `TOKEN_ENCRYPTION_KEY` 变成必填，现有的本地 `.env` 要补上才能启动。
- 全局限速意味着有人从很多 IP 持续尝试时，你自己的登录也会被挡住（每次最多挡 60 秒，不会永久锁死）；已登录的设备不受影响。

**Neutral**:
- 新增后端依赖：`argon2-cffi`、`pyotp`、`cryptography`；前端依赖：`qrcode.react`。
- spec 0001 里「加密算法由第 6 项决定」改为由本 spec 决定，第 6 项沿用。

## Follow-up

- [ ] spec 0001 的 Configuration required 里 `TOKEN_ENCRYPTION_KEY` 的说明改成「格式和算法见 spec 0004，必填」（由 `/sync` 标记）。
- [ ] spec 0003 的 Follow-up「后端统一错误格式定下来后在 `toUiError` 里解析」由本 spec 第 4 步完成。
- [ ] 第 9 项部署：首次上线后的步骤里写上「运行 `setup_code` 并在 1 小时内完成设置」，以及 `TOKEN_ENCRYPTION_KEY` 要单独备份（丢了 TOTP 和以后的 Discord token 都解不开）。
- [ ] 以后如果需要：会话列表和单独踢设备、网页改密码、备用码、passkey。
- [ ] 根目录 `AGENTS.md` 的 Rules 补一条：新的 `/api` 路由默认受保护，公开接口要加进白名单（由 `/sync` 写）。
