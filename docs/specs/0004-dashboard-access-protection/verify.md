# Verify: Dashboard 访问保护 · spec 0004 · updated 2026-11-01
_Steps derived from spec 0004 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

本地准备：`.env` 里有 `TOKEN_ENCRYPTION_KEY`、`SITE_ORIGIN=http://localhost:8080`、`SESSION_COOKIE_SECURE=false`；`docker compose up -d --force-recreate caddy`，重启 api，从 `http://localhost:8080` 打开。

## UI / manual
- [x] 全新数据库，打开 `/` → 先全屏加载，不闪现侧栏，然后跳 `/setup` → AC-13
- [x] 设置页输入错误设置码 → 提示设置码无效，停在第 1 步 → AC-3
- [x] 输入正确设置码，密码填 11 个字符 → 提示长度不对，不往下走；填全角字符（如 12 个 `ａ`）能通过，之后用半角也能登录 → AC-4
- [x] 第 3 步用手机验证器扫二维码，能添加；手动输入密钥也能添加 → AC-4
- [x] 第 4 步输错验证码 → 提示验证码不对，数据库里没有 `dashboard_credential` → AC-4
- [x] 输入正确验证码 → 进首页并已登录；手机时间略有偏差也能通过 → AC-4, AC-5
- [x] 登录页：密码错，或验证码错 → 提示完全相同的「密码或验证码不对」 → AC-5
- [x] 同一个验证码登录成功后，登出，马上再用它登录 → 被拒绝 → AC-5
- [x] 打开 `/settings/security`（未登录）→ 跳 `/login?next=…`，登录后回到安全页 → AC-13
- [x] 手动打开 `/login?next=//evil.com` 并登录 → 回首页，不离开站点 → AC-13
- [x] 已登录时打开 `/login` 和 `/setup` → 直接去首页 → AC-13
- [x] 停掉 api，刷新页面 → 显示「无法连接服务器」和「重试」，不跳转；恢复后点重试能进入 → AC-13
- [x] 登录后在另一个标签页登出，留在第一个标签页 → 最多约 20 秒内跳登录页并提示「登录已过期，请重新登录」 → AC-14
- [x] 图标栏底部有「安全」和「登出」；点登出 → 跳 `/login`，用浏览器后退也看不到数据 → AC-8, AC-15
- [x] 安全页显示最近记录：时间、IP、结果是中文；服务器命令产生的记录 IP 显示「服务器命令」；过长的浏览器标识被截断，悬停看全文 → AC-11

## Commands
- [x] `cd backend && uv run pytest` → 全部通过（含路由白名单遍历、限速、日志无密钥） → AC-1 到 AC-17
- [x] `cd web && pnpm test` → 全部通过 → AC-13, AC-14, AC-15
- [x] `curl -i localhost:8080/api/health`（不带 cookie）→ 401，`error.code` 是 `unauthenticated` → AC-1
- [x] `curl -i localhost:8080/api/livez` 不带 cookie → 200 `{"status":"ok"}`；停掉 postgres 后仍是 200，而带任意 cookie 的 `/api/health` 变成 503 `database_unavailable` → AC-1
- [ ] 线上 `curl -i https://<域名>/api/docs` 和 `/api/openapi.json` → 404 → AC-1
- [x] 登录前 `uv run python -m harvester.api.setup_code` → 打印设置码，日志和库里都没有明文；设置完成后再运行 → 拒绝并提示先重置 → AC-3
- [x] 把 `dashboard_setup_code.expires_at` 改到过去，再调 `POST /api/auth/setup/begin` → 403 `setup_code_invalid` → AC-3
- [ ] 登录后查响应头：名字、`HttpOnly`、`SameSite=Strict`、`Path=/`、`Max-Age=2592000`；线上是 `__Host-harvester_session` 且带 `Secure`、不带 `Domain` → AC-6
- [x] 查 `dashboard_session.token_hash`：是 cookie 值的 SHA-256，不是 cookie 值 → AC-6
- [x] 把会话的 `last_seen_at` 改到 7 天前，或 `created_at` 改到 30 天前 → 下一次请求 401；连续请求时 `last_seen_at` 60 秒内只变一次 → AC-7
- [x] 同一 IP 连续输错 5 次 → 第 6 次 429，带 `Retry-After: 60` 和 `retry_after_seconds`，此时正确密码也进不去；1 分钟后再错一次，等待变成 2 分钟 → AC-9
- [x] 一次并发 10 个错误登录 → `bad_credentials` 正好 5 条，其余 429 → AC-9
- [x] 被限速期间连续请求 → 60 秒内 `rate_limited` 只有 1 条 → AC-11
- [x] `curl -XPOST` 登录不带 `Origin`、带 `Origin: null`、带别的来源 → 都是 403 `bad_origin`，不写 `login_event` → AC-10
- [x] 登录成功后：超过 180 天的 `login_event` 和过期会话被删掉 → AC-11
- [x] `uv run python -m harvester.api.auth_reset`，输入别的 → 取消；输入 `RESET` → 状态回到 `setup_required`，旧 cookie 401，记一条 `ip` 为空的 `reset` → AC-12
- [x] 不设 `TOKEN_ENCRYPTION_KEY`，或设成格式错误的值，启动 api 和 collector → 报错退出，报错里没有密钥原文；`alembic upgrade head` 不受影响 → AC-16
- [x] 用当前密钥加密的 `totp` 密文按 `discord_token` 用途解密 → 失败；换密钥并把旧的放进 `TOKEN_ENCRYPTION_OLD_KEYS` → 仍能登录 → AC-16
- [x] `SESSION_COOKIE_SECURE=false` 且 `SITE_ORIGIN=http://localhost.evil.com` 启动 api → 报错退出 → AC-17
- [x] 提交过短的密码 → 422 响应里搜不到提交的值，字段只有 `loc` 和 `type` → AC-17
- [x] 经 Caddy：70KB 请求体 → 413；`/api/auth/state` 带 `Cache-Control: no-store`；页面带 `Content-Security-Policy: frame-ancestors 'none'`；请求自带伪造的 `X-Forwarded-For` 时，`login_event.ip` 仍是真实来源 → AC-18

## Value sourcing
- [x] 当前会话：改掉 cookie 一个字符 → 401 → Value sourcing「当前会话」
- [ ] 客户端 IP：线上从两个网络登录，安全页显示各自的公网 IP，不是 Docker 网关 → Value sourcing「客户端 IP」
- [x] 浏览器标识：发 2000 字符的 `User-Agent` → 库里 512 个字符 → Value sourcing「浏览器标识」
- [x] 连续失败次数：25 小时前的失败不计；这个 IP 成功后清零；别的 IP 成功不影响 → Value sourcing「限速」
- [x] 全局失败次数：60 秒内不同 IP 共 31 次失败 → 全新 IP 也 429；61 秒后恢复；成功登录不计入 → Value sourcing「全局失败次数」
- [x] TOTP 时间步：前一步、当前步、后一步的验证码能用，差两步不能；用过后一步，再用当前步被拒绝 → Value sourcing「TOTP 时间步」
- [x] 密码长度：`ﬃ`（NFKC 后 3 个码点）按 3 算，emoji 按 1 个算 → Value sourcing「密码长度」
- [x] `otpauth_uri`：验证器里显示的发行方是 Harvester，账号名是 dashboard → Value sourcing「otpauth_uri」
- [ ] 允许的来源：`SITE_ORIGIN=HTTPS://Dash.Example.com:443/` 规范化成 `https://dash.example.com`，浏览器请求能通过 → Value sourcing「Origin 检查」
- [x] 加密密钥：密文第一个字节是 `TOKEN_ENCRYPTION_KEY` 的版本号 → Value sourcing「加密」
- [x] 登录已过期提示：`/login?reason=expired` 有提示，不带参数没有 → Value sourcing「登录页提示」

## Acceptance-criteria coverage
- AC-1：Commands 第 1、3、4、5 步 · AC-2：`pytest` 的路由遍历和黑盒测试 · AC-3：UI 第 2 步，Commands 的设置码和过期 · AC-4：UI 第 3 到 6 步 · AC-5：UI 第 6 到 8 步，Value sourcing TOTP · AC-6：Commands 的 cookie 两步 · AC-7：Commands 的会话过期 · AC-8：UI 登出，Commands 的 `pytest` · AC-9：Commands 的限速两步，Value sourcing 限速 · AC-10：Commands 的 Origin · AC-11：UI 安全页，Commands 的记录两步 · AC-12：Commands 的重置 · AC-13：UI 第 1、9 到 12 步 · AC-14：UI 第 13 步 · AC-15：UI 第 14 步 · AC-16：Commands 的密钥两步 · AC-17：Commands 的 localhost 和 422 · AC-18：Commands 的 Caddy
