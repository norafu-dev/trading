# 0005. Discord 账号连接

**Date**: 2026-11-01
**Status**: Proposed

## Summary

你在 dashboard 里贴上小号的 token，api 把它加密存库，collector 收到通知后用它登录，并保持隐身在线。断网时 collector 自己退避重连，优先恢复原会话；token 失效、需要验证或身份不符时就停止登录，并在界面上告诉你该做什么。真正发出的全新登录每 24 小时最多 10 次，计数存在数据库里，进程崩溃重启和手动操作都绕不过去，单纯断网不消耗额度。这一版只有一个账号，不同步频道（第 7 项做），也不存消息（第 8 项做）。

## Requirements

**User stories**:
- 作为你，我想在 dashboard 贴上 token 后看到小号上线，这样不用碰服务器。
- 作为你，我想让断线后自动恢复，并且在需要我处理时看到明确提示和处理办法。
- 作为你，我想让 token 只以密文存在，界面和日志都拿不到原文。
- 作为你，我想让程序出错时也不会反复登录，这样不会增加封号风险。
- 作为你，我想在小号被封后换一个新小号，并保留已采集的数据和频道开关。

**Acceptance criteria**:
- **AC-1**: 库里没有账号时，collector 正常运行并空转；在设置页提交备注名和有效 token 后，状态依次显示 `connecting` 和 `online`，不用重启任何进程。从提交到 collector 开始连接不超过 5 秒。
- **AC-2**: 登录成功后，`discord_user_id` 和 `discord_username` 被写入，页面显示用户名。Discord 确认登录（READY）后，小号被设为 `invisible`，用另一个账号看到它稳定后是离线状态；上线瞬间的显示在 `/check verify` 里实测并记录。
- **AC-3**: 已有账号时再次添加返回 409 `account_exists`；数据库的唯一索引也挡得住并发添加。
- **AC-4**: token 只以 `discord_token` 用途的 AES-256-GCM 密文存库；所有接口响应、OpenAPI 响应模型、前端状态和日志（包括 `discord` 库自己的日志）都不出现 token 原文或密文。
- **AC-5**: 网络中断或 5xx 时，状态变成 `reconnecting`，按 5 秒起翻倍、封顶 5 分钟的节奏重试，每次带随机抖动，不放弃。从 READY 或 RESUMED 算起稳定 5 分钟后，退避计数清零。恢复后变回 `online`，页面无需手动操作。
- **AC-6**: 能 RESUME（恢复原会话）时，重连不消耗登录额度，也不关闭覆盖段；不能 RESUME 时，走额度检查后重新 IDENTIFY（全新登录），并按断线时间关闭覆盖段，登录成功后重新打开。断线期间不会被记成已覆盖，即使中途换 token、停用、额度用完或进程退出也一样。
- **AC-7**: 任何 24 小时滚动窗口内，最多真正发出 10 次 IDENTIFY（包括库内部的重新 IDENTIFY）。网络没连上、IDENTIFY 没发出的重试不计数。额度用完时不发任何登录请求，状态变成 `login_paused`，`retry_at` 是额度恢复时间，到点后自动继续。进程重启、换 token 和手动重连都不能绕过。
- **AC-8**: 登录返回 401 或网关返回 close code 4004 时，状态变成 `token_invalid`，collector 不再登录；界面提示“token 失效或账号被封”，并引导你到 Discord 确认后换 token。这一版不写 `banned`。
- **AC-9**: Discord 要求人机验证或账号验证时（`CaptchaRequired`、登录返回 403，或者 READY 和后续事件里的 `required_action` 非空），collector 断开，状态变成 `verification_required`，collector 不解验证码、不再登录；你在官方客户端处理后点“重新连接”才会再试。
- **AC-10**: 更换 token 后立即断开旧会话并用新 token 连接。新 token 属于同一个 Discord 账号时变回 `online`；属于另一个账号时，在 IDENTIFY 之前就断开并变成 `account_mismatch`，不消耗额度，身份和权限记录不变，界面提供“确认换成这个账号”。
- **AC-11**: 确认换身份后，`discord_user_id`、`discord_username` 清空，这个账号的 `account_channel_access` 被删除并重新连接；消息、频道开关和 `collector_account_id` 不变。只有 `account_mismatch` 状态能执行，其他状态返回 409。
- **AC-12**: 停用后 5 秒内断开，状态变成 `disabled`，进程重启后也不登录；启用后变回 `connecting`。停用时更换 token 只保存，不连接。
- **AC-13**: 你的操作不会被旧状态覆盖：操作和 collector 写状态同时发生时，最终状态反映你的操作。
- **AC-14**: collector 不可用时不会有两个会话：专用锁连接断开时进程立即退出；重启并重新拿到锁后才会登录。
- **AC-15**: 状态变化后 5 秒内出现在设置页和侧栏。SSE 断开时最多 30 秒内由轮询补上。状态是 `online` 但心跳超过 120 秒，或者状态是 `connecting` 超过 120 秒时，界面显示“采集进程无响应”。
- **AC-16**: 除存活检查外的规则继续生效：所有新接口没登录返回 401，跨站请求被 Origin 检查拒绝。
- **AC-17**: 收到 SIGTERM 后，collector 在 10 秒内关闭会话、关好覆盖段并退出，不改账号状态；下次启动时状态归一。
- **AC-18**: 新增代码通过 `pyright` strict、`ruff`、Biome、`tsc`。只有 `collector/discord_client.py` 导入 `discord`。

## Decision

**Chosen option**: Option 1: collector 单会话监督循环，边界重写 `connect`，状态写入带版本号，额度存库

api 只负责校验、加密和通知，collector 是唯一连接 Discord 的进程；它通过一个 `Client` 子类掌管重连节奏和 IDENTIFY 关卡，所以退避和额度都能精确生效。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### Data model sketch

一个只新增的迁移。

**`discord_account` 的改动**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | text | 是 | `CHECK` 增加 `verification_required`、`login_paused`、`account_mismatch`。`banned` 保留，这一版不写入 |
| `discord_username` | text | 否 | 登录成功后写入，换身份时清空 |
| `retry_at` | timestamptz | 否 | 只在 `login_paused` 时有值 |
| `config_version` | integer | 是 | 默认 1。api 每次改 token、启停、重连、换身份时加 1；改备注名不加 |
| `last_error` | text | 否 | 改成只存固定错误码（见「失败分类表」），恢复 `online` 时清空 |

增加 `CHECK ((status = 'login_paused') = (retry_at IS NOT NULL))`。api 的所有写操作（改备注名除外）都清空 `last_error` 和 `retry_at`。

唯一索引 `uq_discord_account_single`，建在 `((true))` 上，保证最多一行。以后做多账号时用迁移删掉。

**`discord_login_attempt`（新表）**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint identity | 是 | 主键 |
| `account_id` | bigint | 是 | 外键 → `discord_account`，`ON DELETE CASCADE` |
| `attempted_at` | timestamptz | 是 | 默认 `now()` |

索引 `(account_id, attempted_at)`。每行代表一次真正要发出的 IDENTIFY。每次预留额度时顺手删掉 7 天前的行。

### State transitions

| 从 | 到 | 谁触发 | 条件 |
|---|---|---|---|
| 无账号 | `connecting` | api | 添加账号 |
| `connecting`、`reconnecting` | `online` | collector | 处理完 READY；或者这个会话已处理过 READY 后收到 RESUMED。写 `online` 的语句同时写 `last_heartbeat_at = now()` |
| `online` | `reconnecting` | collector | 收到 `on_disconnect`（主动关闭除外） |
| `connecting`、`reconnecting` | `reconnecting` | collector | 可重试的失败（更新 `last_error`） |
| `connecting`、`reconnecting` | `login_paused` | collector | 额度用完（`last_error` = `login_budget_exhausted`） |
| `login_paused` | `connecting` | collector | 到达 `retry_at`，包括重启时已经过期。再次预留失败就写新的 `retry_at`，保持 `login_paused` |
| `connecting`、`reconnecting`、`online` | `token_invalid` | collector | 见失败分类表 |
| `connecting`、`reconnecting` | `verification_required` | collector | 见失败分类表 |
| `connecting`、`reconnecting` | `account_mismatch` | collector | `login` 返回的用户 ID 和 `discord_user_id` 不同（`last_error` = `account_mismatch`） |
| `online` | `verification_required` | collector | `required_action` 非空 |
| `online`、`reconnecting` | `connecting` | collector | 启动时归一（上次进程留下的状态） |
| 除 `disabled` 外的任意状态 | `connecting` | api | 更换 token |
| `token_invalid`、`verification_required`、`banned` | `connecting` | api | 重新连接 |
| `account_mismatch` | `connecting` | api | 确认换身份 |
| 任意状态 | `disabled` | api | 停用 |
| `disabled` | `connecting` | api | 启用 |

规则：

- api 改变状态时，`status_changed_at` 总是重置，所以在 `connecting` 里换 token 会重新开始 120 秒计时。
- 幂等：对 `disabled` 再次停用返回 200，不加版本号，也不发通知。
- collector 写状态得到 `stale_version` 时，一律立即结束当前会话并重读账号；启动归一失败也一样。

### 失败分类表

边界函数把异常转换成 frozen dataclass `ConnectionFailure`（`kind`、`http_status`、`close_code`、`retry_after`），纯函数 `classify_failure` 从上到下取第一条命中的行。`CaptchaRequired` 是 `HTTPException` 的子类，必须排在 HTTP 状态判断前面。

| 信号 | 状态 | `last_error` | 动作 |
|---|---|---|---|
| `CaptchaRequired` | `verification_required` | `captcha_required` | 停止登录 |
| `LoginFailure` 或 HTTP 401 | `token_invalid` | `http_401` | 停止登录 |
| close code 4004 | `token_invalid` | `gateway_close_4004` | 停止登录 |
| 密文解不开 | `token_invalid` | `token_undecryptable` | 不发请求，界面提示检查 `TOKEN_ENCRYPTION_KEY` 或重新填写 token |
| `required_action` 非空 | `verification_required` | `required_action` | 断开并停止登录 |
| 登录时 HTTP 403 | `verification_required` | `http_403` | 停止登录 |
| HTTP 429 或 `RateLimited` | `reconnecting` | `http_429` | 等待 `max(退避, retry_after)` |
| `ReconnectWebSocket`（断网、接收超时、可处理的 close code、opcode 7、INVALID_SESSION） | `reconnecting` | 见下 | 走 `next_delay`；能 RESUME 就 RESUME，否则 IDENTIFY |
| HTTP 5xx、`OSError`、`aiohttp.ClientError`、`CurlError`、超时、`GatewayNotFound`（主要在握手和 `login` 阶段） | `reconnecting` | `network_error` 或 `http_5xx` | 同上 |
| 其余 close code（包括 4008、4010 到 4014） | `reconnecting` | `gateway_close_<code>` | 退避；库标记为不可恢复的 code 必须新建会话，受额度限制 |
| 其他异常 | 不改状态 | | 记录日志并让进程崩溃，Compose 重启后受额度限制 |

`ReconnectWebSocket` 不带 code，所以按这个顺序取错误码：`ws._close_code` 非空表示本端按服务端指令关闭，记 `gateway_reconnect`（opcode 7 和 INVALID_SESSION(true) 不区分）；否则读 `ws.socket.close_code`，非空就记 `gateway_close_<code>`；都为空就记 `network_error`。

### 监督循环

`collector/supervisor.py` 一次只跑一个会话。启动时拿锁，如果有账号就调用 `close_stale_coverage`。之后循环读取账号，按状态决定动作：

- 没有账号，或者状态是 `disabled`、`token_invalid`、`banned`、`verification_required`、`account_mismatch`：不登录，等待账号变化。
- `login_paused`：等到 `retry_at` 或者账号变化。
- 其余状态：解密 token，只读检查额度（用完就进入 `login_paused`，不发任何请求），然后新建 client 开始会话。

启动时，如果最近一条 `attempted_at` 不到 60 秒，先等满 60 秒，防止崩溃循环连续消耗额度。`login_paused` 的等待时长用数据库 `now()` 和 `retry_at` 算出。

**一个会话的顺序**：`login`（只是一次 REST 请求）→ 比对身份，不符就进入 `account_mismatch` 并关闭 → `connect` → `before_identify_hook` 预留额度 → IDENTIFY。`login` 阶段的网络失败在同一个 client 上退避重试。

**账号变化**来自 `LISTEN discord_account_changed`（复用锁连接，断开即退出；先 LISTEN 再第一次读账号，避免漏通知）或者 30 秒一次的轮询。collector 只比较 `config_version`；版本变了就立刻关闭当前会话，关闭开放的覆盖段，然后重新读取。

**`discord_client.py`** 是唯一导入 `discord` 的文件，包含：

- `HarvesterClient(discord.Client)`：**每个会话新建一个**，不用 `clear()`，所有退出路径都 `await close()`。
- 覆盖 `connect`，只负责等待和 RESUME 判定：保留库的流程（`DiscordWebSocket.from_client` 和 `ws_params`），所有等待都走注入的退避。只有 `self.ws` 不为空且 `session_id`、`sequence` 都不为空时才 RESUME，否则 IDENTIFY。
- 覆盖 `before_identify_hook`：调用注入的额度预留，用完就抛内部异常 `LoginBudgetExhausted`，由监督循环接住。这时新 socket 已连上但还没赋给 `self.ws`，要确认 `close()` 能释放它，并加测试。
- 事件处理器（`on_ready`、`on_resumed`、`on_disconnect`、`required_action` 更新）**不写库**，只把带会话代号的事件放进 `asyncio.Queue`。监督循环按序消费并写库，丢弃代号不对的事件。写库失败时退出进程，由 Compose 重启。
- 客户端选项：`status=Status.invisible`、`sync_presence=False`（不改账号设置）、`chunk_guilds_at_startup=False`、`max_ratelimit_timeout=60.0`，不设置 `captcha_handler`。
- 把 `discord` logger 固定在 `WARNING`，不跟随 `LOG_LEVEL`，避免请求细节进日志。

**退避**：纯函数 `next_delay(attempt, random)` 返回 `min(300, 5 × 2^(attempt-1)) × uniform(0.8, 1.0)`。`attempt` 放在监督循环，跨 client 保留，`config_version` 变化时清零；从 READY 或 RESUMED 算起稳定满 300 秒时清零。`ReconnectWebSocket` 时，如果上一次连接已稳定 60 秒以上就立即重连，否则走退避。重新 IDENTIFY 之前总是走退避。

**心跳**：`online` 时每 30 秒更新 `last_heartbeat_at`，写 `online` 时同步写一次。单次心跳写入失败只记日志；数据库真的断了时，由锁连接探测触发退出。

**覆盖段**：严格按 spec 0002 的覆盖规则。`on_ready`（全新登录）时，给 `collect_enabled` 且 `collector_account_id` 是自己的频道各打开一段。RESUME 成功不动。第 6 项完成时这个频道集合还是空的，第 7 项开始才有内容。

监督循环记录一个「未恢复的断线时间」：没有值时，收到第一次 `on_disconnect` 就记下；之后失败重连再派发的 `on_disconnect` 不覆盖；只在 RESUMED 或新会话 READY 时清除。

| 会话结束原因 | 覆盖段关闭时间 |
|---|---|
| 不能 RESUME 而重新 IDENTIFY（在预留额度之前关闭） | 未恢复的断线时间 |
| 换 token、停用、版本变化、身份不符、需要验证 | 未恢复的断线时间；没有就用当前时间 |
| 会话中 4004、不可恢复的 close code、额度用完 | 未恢复的断线时间；没有就用当前时间 |
| SIGTERM | 未恢复的断线时间；没有就用当前时间 |
| 崩溃或被强杀 | 下次启动的 `close_stale_coverage` |

**SIGTERM**：取消会话任务，关闭 client，按上表关覆盖段，不改状态，整体限时 10 秒（短于 Compose 默认的强杀时限）。

### API surface

都在 `/api/discord/account` 下。全部要求登录会话，写操作经过 Origin 检查，错误用 spec 0004 的统一格式。写操作和 `pg_notify` 在同一个事务里。

| Endpoint | Method | Key inputs | Key outputs | Key errors |
|---|---|---|---|---|
| `/api/discord/account` | GET | | `{ account: DiscordAccountOut \| null }` | 401 |
| `/api/discord/account` | POST | `label`, `token` | 201 `DiscordAccountOut` | 409 `account_exists`，422 |
| `/api/discord/account/label` | PUT | `label` | `DiscordAccountOut` | 404 `account_not_found`，422 |
| `/api/discord/account/token` | PUT | `token` | `DiscordAccountOut` | 404，422 |
| `/api/discord/account/disable` | POST | | `DiscordAccountOut` | 404 |
| `/api/discord/account/enable` | POST | | `DiscordAccountOut` | 404，409 `invalid_account_state` |
| `/api/discord/account/reconnect` | POST | | `DiscordAccountOut` | 404，409 `invalid_account_state` |
| `/api/discord/account/switch-identity` | POST | | `DiscordAccountOut` | 404，409 `invalid_account_state` |
| `/api/events` | GET（已有） | | 新增事件 `account_status`，没有数据载荷 | |

`DiscordAccountOut`：`id`、`label`、`discord_user_id`（字符串，避免 JS 丢精度）、`discord_username`、`status`、`status_changed_at`、`last_error`、`last_heartbeat_at`、`retry_at`、`collector_stale`。模型没有任何 token 字段。

**校验**：`label` 去掉首尾空白后 1 到 100 字符。`token` 用 `SecretStr` 接收，去掉首尾空白后 20 到 512 字符，只允许可见 ASCII，不能含空格（所以 `Bot xxx` 会被拒绝），否则返回 422 `token_format_invalid`。

### 通知

一个通道 `discord_account_changed`，载荷 `{"account_id": <id>}`。

- api 写操作和 collector 的状态写入都发通知；心跳不发。
- `notifications.py` 增加监听这个通道，`Broadcaster` 的载荷带 `type` 区分事件，`/api/events` 分别输出 `new_message` 和 `account_status`。
- 前端收到 `account_status`，或者 SSE 重新连上时，让 `["discord-account"]` 查询失效。另外每 30 秒轮询一次，让 `collector_stale` 在 collector 挂掉、没有事件时也会更新。

### 界面

- **设置页** `web/src/app/(dashboard)/settings/discord/page.tsx`：没有账号时显示添加表单，并提示“selfbot 违反 Discord 服务条款，请使用小号”。有账号时显示状态卡片和操作。
- **侧栏**：`system-status.tsx` 增加账号状态指示，点击进入设置页。
- **token 输入框**：`type="password"`、`autocomplete="off"`，提交后清空，从不回填。
- 接口错误走现有的 `toUiError`。

| 状态 | 文案 | 可用操作 |
|---|---|---|
| `connecting` | 正在连接 | 停用 |
| `online` | 在线（隐身） | 更换 token、停用 |
| `reconnecting` | 连接中断，正在重试 | 更换 token、停用 |
| `login_paused` | 已暂停登录，将在 {retry_at} 恢复 | 更换 token（不提前连接）、停用 |
| `token_invalid` | token 失效或账号被封，请到 Discord 确认 | 更换 token、重新连接、停用 |
| `verification_required` | Discord 要求验证，请用官方客户端处理 | 重新连接、更换 token、停用 |
| `account_mismatch` | 这个 token 属于另一个 Discord 账号 | 确认换成这个账号（需要二次确认）、更换 token、停用 |
| `banned` | 账号已被封 | 更换 token、停用 |
| `disabled` | 已停用 | 启用、更换 token |
| `collector_stale` 为真 | 采集进程无响应（叠加显示） | 同当前状态 |

`token_undecryptable` 时，`token_invalid` 的文案换成“token 无法解密，请检查主密钥或重新填写”。

### Value sourcing

| 动作 | 值 | 来源 |
|---|---|---|
| 添加、换 token | `token_ciphertext` | `crypto.encrypt(key_ring, PURPOSE_DISCORD_TOKEN, token)`，新增常量 `"discord_token"` |
| 添加、换 token | `token_key_version` | `key_ring.current_version` |
| 登录 | token 原文 | `crypto.decrypt(...)`，只存在于监督循环的局部变量和客户端内部 |
| `login` 成功后 | `discord_user_id`、`discord_username` | `client.user.id`、`client.user.name`（`login` 完成后就有值，不用等 READY） |
| 退避和崩溃保护 | 最近一次尝试 | `discord_login_attempt.attempted_at` 的最大值 |
| 额度检查 | 窗口内次数 | 数据库 `now()`，统计 `attempted_at > now() - 24 小时` |
| 额度用完 | `retry_at` | 窗口内最早的 `attempted_at` + 24 小时 |
| 退避 | 等待秒数 | `next_delay`；HTTP 429 时和 `retry_after` 取大 |
| 覆盖段关闭 | 断线时间 | 第一次 `on_disconnect` 时注入的时钟 |
| `GET` 响应 | `collector_stale` | 读取时用数据库 `now()` 算：`online` 且 `last_heartbeat_at` 超过 120 秒，或者 `connecting` 且 `status_changed_at` 超过 120 秒；`last_heartbeat_at` 为 `NULL` 时不算过期 |
| 版本校验 | `config_version` | collector 在读取账号时记下；所有状态写入带 `WHERE config_version = :expected` |
| 登录额度和阈值 | 10 次、24 小时、7 天、30 秒、60 秒、120 秒 | `harvester.collector` 和 `harvester.db` 中的具名常量，不是环境变量 |

### 数据库函数

放在 `harvester.db.account`，都不自己提交事务，可预期结果用返回值表达。

- `get_account(session) -> AccountRow | None`
- `create_account(...) -> AccountRow | Literal["exists"]`，依靠唯一索引判断
- `update_label`、`replace_token`、`set_enabled`、`request_reconnect`、`switch_identity`：`config_version + 1` 并发通知；状态不允许时返回 `Literal["invalid_state", "not_found"]`
- `check_login_budget(session, account_id) -> Literal["available"] | LoginBudgetExhausted`：只读，`login` 前调用
- `reserve_login_attempt(session, account_id) -> Literal["reserved"] | LoginBudgetExhausted`：`SELECT ... FOR UPDATE` 锁账号行，统计窗口内次数，够额度才插入，在 `before_identify_hook` 里调用并提交
- `record_status(session, account_id, expected_version, status, last_error, retry_at) -> Literal["updated", "stale_version"]`：只有状态真的变化时才更新 `status_changed_at`
- `record_identity(session, account_id, expected_version, user_id, username) -> Literal["updated", "stale_version", "mismatch"]`：`login` 成功后调用；比对身份，相符就写入
- `record_heartbeat(session, account_id, expected_version) -> None`：只更新 `status = 'online'` 的行

### Key invariants

- 只有 collector 连 Discord，api 不发任何 Discord 请求。
- 一个进程最多一个会话；数据库最多一个账号。
- 只有 collector 解密 token；token 原文不进日志、异常信息、响应或数据库。
- 每次发出 IDENTIFY 之前都已经写入并提交一行 `discord_login_attempt`，所以崩溃也不会少计；没有发出 IDENTIFY 的重试不写。
- 断线期间永远不算已覆盖：覆盖段的关闭时间不会晚于未恢复的断线时间。
- 事件处理器不写库，所有数据库写入都由监督循环按序执行。
- collector 的状态写入都带版本校验；版本不符时放弃写入，不覆盖 api 的操作。
- 进入 `token_invalid`、`verification_required`、`account_mismatch`、`banned`、`disabled` 后，只有 api 操作能让 collector 再次登录。
- 永远不解验证码，也不伪装客户端指纹。遇到验证就停下来。
- 没有删除账号的接口；`account_channel_access` 只在确认换身份时删除。

### Security model

只有你一个使用者，所有接口要求 spec 0004 的登录会话。token 只写不读，所以被盗的会话拿不到原文，但能换 token，这个风险已接受（不要求二次 TOTP）。主密钥沿用 `TOKEN_ENCRYPTION_KEY`，密文附加数据绑定用途名 `discord_token`，不能和 TOTP 密文互换。不涉及 GDPR、PCI 等合规范围。

### Configuration required

不需要新的环境变量。`pyproject.toml` 把 `discord-py-self` 锁定到 `==2.1.0`。

### Critical test scenarios

后端用连真实 PostgreSQL 的集成测试，Discord 用假的 `from_client` 和假客户端；真实账号只在 `/check verify` 里手动验证（包括上线瞬间的隐身显示、403、验证码和 `required_action` 的实际形式）。

- Happy path：添加账号 → 通知到达 → 假客户端 `on_ready` → `online`，身份写入，响应没有 token 字段，verifies **AC-1**、**AC-2**
- 唯一账号：并发两个添加，一个 201 一个 409，verifies **AC-3**
- 泄露检查：跑完整流程后，日志和响应中都搜不到测试 token，verifies **AC-4**
- 退避：`next_delay` 的序列和上限；连接稳定后清零；429 时取 `retry_after`；首次连接失败（`self.ws` 为空）不崩溃并继续重试；断网很久也不写 `discord_login_attempt`，verifies **AC-5**、**AC-7**
- RESUME：断线后 RESUME，额度行数和覆盖段不变；INVALID_SESSION 后调用 `before_identify_hook`，覆盖段按断线时间关闭后重开；断线中途停用，关闭时间仍是断线时间，verifies **AC-6**
- 额度：第 11 次返回 `LoginBudgetExhausted`，`retry_at` 正确；25 小时前的不计；并发预留不超额；重启和换 token 后仍然暂停，verifies **AC-7**
- 分类表：表中每一行都有 `classify_failure` 单元测试，包括错误码的取值顺序；停止类状态后不再调用登录；`required_action` 非空时断开，verifies **AC-8**、**AC-9**
- 换 token：同身份恢复 `online`；异身份在 IDENTIFY 前进入 `account_mismatch`，额度行数和身份都没变；确认后身份和权限行清空，消息和频道开关不变；其他状态返回 409，verifies **AC-10**、**AC-11**
- 停用：会话被关闭，重启后不登录；停用时换 token 不连接，verifies **AC-12**
- 竞争：先读版本，再由 api 停用，然后 collector 写 `reconnecting`，结果是 `stale_version`，状态仍为 `disabled`，会话立即结束；乱序到达的 disconnect 和 resumed 按队列顺序生效；旧会话的事件被丢弃，verifies **AC-13**
- 锁丢失：断开专用连接，进程以 `EXIT_LOCK_LOST` 退出，verifies **AC-14**
- 界面：Vitest 覆盖每个状态的文案和操作，`account_status` 事件触发重新取数，`collector_stale` 叠加显示，verifies **AC-15**
- 权限：每个新接口没登录返回 401，错误 Origin 被拒绝，verifies **AC-16**
- SIGTERM：发信号后 10 秒内退出，client 已关闭，覆盖段按规则关闭，状态没变，verifies **AC-17**
- 边界：测试扫描 `harvester` 包，只有 `collector/discord_client.py` 导入 `discord`，verifies **AC-18**

## Build plan

按 Tracer Bullet：先打通一条真实的线，再加粗失败处理。

1. 细线的数据层：迁移、模型、`PURPOSE_DISCORD_TOKEN`、`harvester.db.account` 的创建和读取，锁定库版本，satisfies **AC-3**、**AC-4**、**AC-18**
2. 细线打通：`POST`、`GET` 接口，设置页的添加表单和状态显示，`discord_client.py`（先不覆盖 `connect`），监督循环读取账号、解密、隐身登录、写 `online` 和身份，事件队列和新建 client 的结构从这一步开始就位，satisfies **AC-1**、**AC-2**、**AC-4**、**AC-16**
3. 变更通知和操作：`discord_account_changed` 通道、collector 的 LISTEN 和轮询、`config_version` 带版本的写入、换 token、启停、改备注名、SSE 事件和侧栏指示、`collector_stale`，satisfies **AC-10**、**AC-12**、**AC-13**、**AC-15**
4. 重连和额度：覆盖 `connect`、`before_identify_hook`、`next_delay`、额度检查和预留、`login_paused`、心跳、覆盖段开关和断线时间、启动时清理残留、SIGTERM 流程，satisfies **AC-5**、**AC-6**、**AC-7**、**AC-14**、**AC-17**
5. 失败处理：`classify_failure`、停止类状态、重新连接、身份不符和确认换身份，以及各状态的界面文案，satisfies **AC-8**、**AC-9**、**AC-10**、**AC-11**
6. 测试和收尾：上面的测试场景、`discord` logger 固定级别、泄露检查和导入边界检查、重新生成 OpenAPI 类型，satisfies **AC-1** 到 **AC-18**

## Consequences

**Positive**:
- 崩溃循环、错误分类和误操作都不会造成反复登录。
- 退避和额度都能精确生效，换小号也不丢数据。
- 第 7 和第 8 项可以直接接上已有的会话、覆盖段钩子和 SSE 通道。

**Negative / tradeoffs**:
- 上线瞬间可能短暂不是隐身，因为库在 READY 之后才设置隐身。
- 覆盖 `connect` 用到了库的内部接口，升级 `discord-py-self` 时要对照上游并重跑测试。
- 每次发布 collector 都消耗 1 次额度。一天发布很多次时会触发 `login_paused`，采集最多可能暂停到额度恢复，这段时间的消息会漏掉，要靠第 10 项补回来。
- 分不清 token 失效和被封，界面只能给出合并的提示。
- 全局 advisory lock 和账号无关，以后做多账号时要改。
- 状态是 `reconnecting` 或停止类时没有心跳，collector 这时挂掉界面看不出来，要等第 9 项的进程级心跳。

**Neutral**:
- 本地和线上的数据库各有各的额度。不要同时用同一个 token 跑本地和线上，会出现两个会话。
- 用一个只新增的迁移改 `status` 的 `CHECK`，并增加三列和一张表，符合 spec 0002 的约定。现在还没有账号行，不需要回填数据。

## Follow-up

- [ ] 第 7 项：写入 `guild`、`channel`、`account_channel_access`。这项工作从本项移过去，spec 0002 Follow-up 里写的“第 6 项写入 `account_channel_access`”由 `/sync` 标记。打开开关时设置 `collector_account_id`，并确认客户端选项能收到大服务器的事件（`guild_subscriptions` 的默认值）。
- [ ] 第 8 项：决定实际发送的 `guild_subscriptions` 设置，并用小号在大服务器上验证收消息。
- [ ] 第 9 项：加进程级心跳，覆盖所有账号状态；部署手册写明发布 collector 会消耗登录额度。
- [ ] spec 0001 和 0002 里“advisory lock 按账号区分”改为“多账号时再区分”（由 `/sync` 标记）。
- [ ] `/develop` 和 `/check verify` 时用真实响应确认 HTTP 403、验证码和 `required_action` 的实际形式，以及上线瞬间的隐身显示。如果和本 spec 不符，回到本 spec 修改；上线瞬间可见的话，到官方客户端预先把账号设成隐身。
