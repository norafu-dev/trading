# Verify: Discord 账号连接 · spec 0005 · updated 2026-11-01
_步骤来自 spec 0005 的验收标准。`/check verify` 跑这些步骤，`/test` 把长期有用的固定下来。标了“小号”的步骤要用真实的小号 token，不要用主号。_

## UI / manual
- [ ] 没有账号时打开 `/settings/discord` → 看到添加表单和服务条款提示，collector 空转 → AC-1
- [ ] 小号：提交备注名和有效 token → 5 秒内开始连接，状态依次是“正在连接”和“在线（隐身）”，不用重启进程 → AC-1
- [ ] 小号：上线后 → 页面显示用户名；用另一个账号看它稳定后是离线状态，并记录上线瞬间的显示 → AC-2
- [ ] 小号：运行中断网，再恢复 → 状态变成“连接中断，正在重试”，恢复后自动回到在线，`discord_login_attempt` 没有新增行 → AC-5, AC-6
- [ ] 小号：运行中重启 collector → 额度多 1 行，60 秒防护生效，状态回到在线 → AC-7, AC-14
- [ ] 提交一个格式正确但无效的 token → 状态变成“token 失效或账号被封”，之后不再发登录请求 → AC-8
- [ ] 小号：如果遇到验证要求，记录 HTTP 403、验证码和 `required_action` 的实际形式 → 状态变成“Discord 要求验证”，不再登录；点“重新连接”才再试 → AC-9
- [ ] 小号：换同一账号的新 token → 回到在线；换另一个小号的 token → 变成“这个 token 属于另一个 Discord 账号”，额度和身份都没变 → AC-10
- [ ] 小号：在上一步点“确认换成这个账号”并二次确认 → 身份更新，权限行清空，消息和频道开关不变 → AC-11
- [ ] 小号：停用 → 5 秒内断开，重启进程也不登录；启用 → 回到正在连接；停用时换 token → 只保存不连接 → AC-12
- [ ] 小号：状态变化时看设置页、侧栏圆点和首页系统状态 → 5 秒内都更新；停掉 Caddy 的 SSE 或断开后最多 30 秒由轮询补上 → AC-15
- [ ] 小号：`kill -9` collector 超过 120 秒 → 叠加显示“采集进程无响应” → AC-15

## Commands
- [ ] `cd backend && uv run pytest tests/collector tests/db/test_account.py tests/api/test_discord_account.py` → 全部通过 → AC-3, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10, AC-11, AC-13, AC-16, AC-17
- [ ] 手动在库里插入 10 行 24 小时内的 `discord_login_attempt`，再重启 collector → 变成 `login_paused`，不发任何请求，`retry_at` 等于最早一行加 24 小时 → AC-7
- [ ] 把 `attempted_at` 改成 25 小时前 → 这些行不再计数，到点自动继续 → AC-7
- [ ] 跑完整流程后 `grep` collector 和 api 日志 → 搜不到测试 token；`psql` 查 `token_ciphertext` → 没有原文 → AC-4
- [ ] 用错误的 Origin 和没登录的请求访问每个 `/api/discord/account` 接口 → 分别被拒绝和返回 401 → AC-16
- [ ] 对运行中的 collector 发 SIGTERM → 10 秒内退出，账号状态不变，开放的覆盖段已关闭 → AC-17
- [ ] 断开锁连接（`pg_terminate_backend`）→ 进程以退出码 4 结束，重启拿到锁后才登录 → AC-14
- [ ] `uv run ruff check . && uv run pyright && pnpm lint && pnpm typecheck` → 通过；`grep -rl "import discord\|from discord" backend/src` → 只有 `collector/discord_client.py` → AC-18

## Value sourcing
- [ ] 换一个 `TOKEN_ENCRYPTION_KEY` 重启 collector → `token_undecryptable`，不发请求（`token_ciphertext` 和 `crypto.decrypt` 的来源）
- [ ] 把数据库时区和浏览器时区设成不同的值 → `retry_at` 和 `collector_stale` 都按数据库 `now()` 判断，界面按浏览器时区显示
- [ ] 让 `client.user` 的用户名变化后重新登录 → `discord_username` 更新
- [ ] 跑 `next_delay` 的上下限，以及 HTTP 429 带 `retry_after` 的情况 → 等待时间取两者较大值
- [ ] 小号：断网超过 5 分钟，再恢复，会话仍有效 → 恢复后一般 30 秒内回到在线，最坏约 90 秒，不新增额度行 → AC-5
- [ ] 小号：断网久到 RESUME 被拒，再恢复 → RESUME 被拒后最多等 30 秒就发出全新登录，额度多 1 行，状态回到在线，覆盖段按断线时间关闭 → AC-5, AC-6, AC-7
- [ ] 把 `last_heartbeat_at` 改成 121 秒前和 `NULL` → `collector_stale` 分别为真和假
- [ ] 改备注名 → `config_version` 不变，会话不断；换 token → 加 1，会话重连

## Acceptance-criteria coverage
- AC-1, AC-2：UI 前三步
- AC-3：Commands 第 1 步
- AC-4：Commands 第 4 步
- AC-5, AC-6：UI 断网步骤
- AC-7：UI 重启步骤，Commands 第 2、3 步
- AC-8, AC-9, AC-10, AC-11, AC-12：UI 对应步骤
- AC-13：Commands 第 1 步（竞争场景）
- AC-14：Commands 锁连接步骤
- AC-15：UI 最后两步
- AC-16：Commands 第 5 步
- AC-17：Commands SIGTERM 步骤
- AC-18：Commands 最后一步
