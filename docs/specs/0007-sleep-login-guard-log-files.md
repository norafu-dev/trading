# 0007. 睡眠期间的登录保护和日志文件

**Date**: 2026-11-02
**Status**: In Progress

## Summary

在本地开发时，Mac 合盖后还会在后台短暂唤醒，collector 可能趁这几十秒发一次全新登录，白白消耗每天 10 次的额度。这份 spec 让 collector 在发现电脑睡过之后，等电脑连续醒满 90 秒才发全新登录，恢复原会话（RESUME，不消耗额度）不受影响。同时加一个可选的 `LOG_DIR`，把 collector 和 api 的日志各存一份文件，方便事后查原因。服务器不会睡眠，也不配置 `LOG_DIR`，所以线上行为不变。

## Context

2026-11-01 到 11-02，账号在 5 小时内用完 10 次额度，暂停了约 18 小时。第 7 到第 10 次登录发生在同一个 collector 进程里（01:02 启动后没有重启），第 8 到第 10 次和被拦下的第 11 次，时间都和 `pmset` 记录的合盖或后台唤醒对得上。每次带网络的后台唤醒约 45 秒，只有 2 秒的唤醒没有产生登录。

推测的过程是：电脑睡着，连接断开，后台唤醒时 RESUME 被拒（会话已过期），于是立刻 IDENTIFY，电脑又睡着，这次登录白费。这个推测还没有直接证据，因为 stdout 日志没有保存，第 9、10 次只隔 47 秒的原因也查不到。

会话真的过期后，全新登录无法避免，能决定的只有时机：要花额度，就等电脑真正醒着的时候再花。

Spec 0005 的额度关卡 `before_identify_hook` 在 `from_client` 内部运行，已经打开了 socket，并且处在 `GATEWAY_CONNECT_TIMEOUT` 60 秒超时之内，所以等待不能放进这个 hook。

## Requirements

**User stories**:
- 作为你，我想让合盖和后台唤醒不再消耗登录额度，这样第二天打开电脑时账号不会处于暂停状态。
- 作为你，我想在事后能查到每次断线和全新登录的原因，这样判断问题时不用猜。

**Acceptance criteria**:
- **AC-1**: collector 发现系统时钟比单调时钟多走了超过 10 秒，就记为发生过一次睡眠，并记录 `collector.sleep_detected`。
- **AC-2**: 发现睡眠后，只有单调时钟连续走满 90 秒且期间没有再次发现睡眠，才能打开要 IDENTIFY 的连接。再次发现睡眠时重新计时。等待期间不写 `discord_login_attempt`，也不打开 socket。
- **AC-3**: RESUME 不受 AC-2 限制，后台唤醒时可以照常尝试。RESUME 成功的行为和 spec 0005 AC-6 一致。
- **AC-4**: 从进程启动起没有发现过睡眠时，登录时机和 spec 0005 完全一致，没有额外等待。
- **AC-5**: 等待期间状态和文案不变：断线后是 `reconnecting`（“连接中断，正在重试”），首次连接是 `connecting`，不加状态、不改界面。换 token、停用或进程退出能立刻打断等待。
- **AC-6**: spec 0005 的额度规则和覆盖规则不变：真正发出 IDENTIFY 前都要预留额度，覆盖段的关闭时间仍是断线时间。
- **AC-7**: 配置了 `LOG_DIR` 时，collector 写 `<LOG_DIR>/collector.log`，api 写 `<LOG_DIR>/api.log`，内容和 stdout 相同（同样的 JSON 行）。没配置时只写 stdout。
- **AC-8**: 单个日志文件到 10 MB 时轮转，保留 5 份旧文件，每个进程最多占约 60 MB。
- **AC-9**: 日志文件和 stdout 的脱敏保证相同：都走 `_redact`，按字段名遮盖；日志目录权限是 `0700`，已存在的目录也会修正；`logs/` 不进 git。
- **AC-10**: `LOG_DIR` 创建不了或写不了时，进程启动时抛异常，以非零状态退出，不静默退回只写 stdout。
- **AC-11**: 日志能回答“为什么发了这次全新登录”：断线记录失败类型和 close code，每次预留额度记录原因，额度用完记录 `retry_at`。这些字段不含 token 和响应内容。

## Options considered

### Option 1: 连续醒满才发全新登录

对比 `time.time()` 和 `time.monotonic()` 发现睡眠，在打开 IDENTIFY 连接前等待。

**Pros**:
- 合盖时也有效，不依赖操作系统命令。
- 服务器不睡眠，不会触发。

**Cons**:
- 打开电脑后如果会话已过期，要多等 90 秒。
- 比这更长的后台唤醒仍会消耗额度。

### Option 2: 用 `caffeinate` 阻止睡眠

不改 collector，开发时阻止系统睡眠。

**Pros**:
- 不用写代码。

**Cons**:
- 合盖通常挡不住，也更费电，还要记得启动。

### Option 3: 读 `pmset` 判断后台唤醒

直接询问 macOS 当前的电源状态。

**Pros**:
- 判断更直接。

**Cons**:
- 只能用于 macOS，还要解析不稳定的命令输出，给服务器代码加了平台分支。

### Option 4: 只靠手动停 collector

合盖前自己停掉。

**Pros**:
- 没有新代码。

**Cons**:
- 忘一次就可能损失大半天，这次就是这样发生的。

## Decision

**Chosen option**: Option 1: 连续醒满才发全新登录

发现睡眠后，collector 要等电脑连续醒满 90 秒才打开 IDENTIFY 连接；日志通过可选的 `LOG_DIR` 存到按大小轮转的文件。

**Implementation skills**: `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`) · `security-review` (`getsentry/skills`, `.claude/skills/security-review/`)

## Rationale

这个问题的根源是额度花在了电脑马上又要睡着的时候，所以判断条件定在“电脑是否持续醒着”，而不是“网络通不通”。后台唤醒时网络往往是通的，只看网络挡不住。两个时钟的差值是最简单的信号：`time.monotonic()` 在 macOS 上用 `mach_absolute_time()`，已确认睡眠时不走；`time.time()` 继续走。

等待放在打开 socket 之前，因为 hook 里等 90 秒会先撞上 60 秒连接超时，还会让连接一直空闲。这样打开连接到 IDENTIFY 之间仍有很小的空隙，但这里有意不做二次检查：刚连续醒满 90 秒就又睡着的情况很少见，最多损失 1 次额度，而检查要额外处理 socket 清理、状态写入、退避计数、覆盖段时间和 RESUME 参数，代价不值得。

`LOG_DIR` 配目录而不是文件，因为两个进程共用 `.env`，共用一个 `RotatingFileHandler` 文件会在轮转时互相破坏。这里不用更重的日志工具：线上已经有 Docker 的 stdout 日志，这个功能只是补本地的缺口。

## Feature design

**Data model sketch**: 不改表结构，没有迁移。

**State transitions**: 不变。等待期间账号保持 `reconnecting`；如果当前是 `connecting`（比如切换 token 后的第一次连接），就保持 `connecting`。

**API surface**: 不变。

**组件和接缝**:

- `harvester/collector/wake_guard.py`（新文件）：
  - 纯函数 `observe(state, wall, mono) -> WakeState`：`state` 是 frozen dataclass，存上一次的 `wall`、`mono` 和最近一次发现睡眠的 `mono`（没有就是 `None`），差值的计算只在这里。
  - 纯函数 `seconds_until_settled(state, mono) -> float`：从未发现睡眠时返回 `0`。
  - `WakeGuard`：持有当前状态的小类（可变状态只在这一处，由 `run` 创建一个并传下去，不放模块级），提供 `sample()` 和 `async wait_until_settled()`。`wait_until_settled` 每次循环先采样，所以刚醒时不会读到旧状态；每 5 秒至少采样一次。它靠任务取消退出，不检查连接状态：`run_session` 退出时会取消会话任务。时钟函数显式传入，测试用假时钟。
  - 后台任务 `watch_sleep(guard)`：每 5 秒调用一次 `sample()`，只为及时记录 AC-1 的日志；是否放行由 `wait_until_settled` 重新采样决定。它由 `collector/main.py` 启动，和其他后台任务一起退出。
  - `observe` 只在差值超过阈值时算睡眠。系统时间向前跳或事件循环卡住可能造成误判，但只会多等 90 秒；时间向后跳差值为负，忽略。
- `discord_client.py`：`HarvesterClient` 新增参数 `guard: WakeGuard | None = None`，`None` 时不等待。`connect` 循环在调用 `_ws_factory` 之前，如果这次 `params` 不带 `resume`，先 `await guard.wait_until_settled()`。顺序是先走完 `_wait_before_retry` 的退避，再等 90 秒，两段时间叠加，也就是最多 5 分钟加 90 秒，只影响 IDENTIFY。
- `connect` 还要记录 `self._resume_rejected`：上一次连接带 resume 发起并收到 `ReconnectWebSocket(resume=False)` 时为真，下一次连接之后清除。`before_identify_hook` 把它放进 `IdentifyRequested` 的新字段 `resume_rejected`（`events.py`）。
- `main.py`：用 `functools.partial(new_client, guard=guard)` 作为 `new_session`。`Deps` 的类型和 `run_session` 不变，现有测试的假对象不用改。
- `supervisor.py`：`_on_identify` 预留成功时记录 `collector.identify_reserved`，额度用完时记录 `collector.login_budget_exhausted`；`_on_retry` 和 `_on_ended` 处理失败时记录 `collector.connection_failed`。
- `logging.py`：`configure_logging(level, process=None, log_dir=None)`。`process` 和 `log_dir` 都不为空时创建目录，并显式 `chmod 0o700`（已存在的目录也修正），加一个 `RotatingFileHandler`，复用 stdout handler 的 formatter。`delay=False`，打不开就抛异常，由 `__main__.py` 不捕获，进程以非零状态退出。替换 root handlers 之前先关闭旧的，这样重复调用不会泄漏文件句柄。现有只传 `level` 的调用不受影响。
- `config.py`：`log_dir: Path | None = None`。相对路径按进程工作目录解析，按项目命令从 `backend/` 启动时就是 `backend/logs`。两个 `__main__.py` 读取 `settings.log_dir`，连同 `collector` 或 `api` 一起传入。

**常量**（具名常量，不是环境变量，和 spec 0005 一致）：

| 常量 | 值 | 位置 |
|---|---|---|
| `SLEEP_GAP_SECONDS` | 10 | `wake_guard.py` |
| `SETTLE_SECONDS` | 90 | `wake_guard.py` |
| `WATCH_INTERVAL_SECONDS` | 5 | `wake_guard.py` |
| `LOG_MAX_BYTES` | 10 × 1024 × 1024 | `logging.py` |
| `LOG_BACKUP_COUNT` | 5 | `logging.py` |

**Value sourcing**:

| Action | Value | Source |
|---|---|---|
| 判断睡眠 | 两次采样之间的差值 | `(wall - 上次 wall) - (mono - 上次 mono)`，来自 `time.time()` 和 `time.monotonic()`，大于 `SLEEP_GAP_SECONDS` 就算睡眠 |
| 判断能否 IDENTIFY | 剩余等待 | `SETTLE_SECONDS - (mono - 最近一次发现睡眠的 mono)`；从未发现睡眠时为 0 |
| 选择是否等待 | 下一次是否 IDENTIFY | 现有 `_next_params` 的结果不带 `resume` |
| `collector.identify_reserved.reason` | `resume_rejected`、`after_disconnect`、`initial` | `_on_identify` 按顺序判断：`event.resume_rejected` 为真是 `resume_rejected`；否则 `state.loop.disconnected_at` 有值是 `after_disconnect`；其余是 `initial` |
| `collector.sleep_detected.gap_seconds` | 睡了多久，单位秒 | 差值 |
| `collector.connection_failed` | `kind`、`close_code`、`http_status` | 现有 `ConnectionFailure` |
| `collector.login_budget_exhausted.retry_at` | 恢复时间 | 现有 `LoginBudgetExhausted.retry_at` |
| 日志路径 | 文件路径 | `Settings.log_dir` 加进程名（`__main__.py` 传入 `collector` 或 `api`） |
| 账号状态和文案 | `reconnecting` 文案 | 现有状态和前端映射，不改 |

**Key invariants**:
- 等待一定发生在打开 IDENTIFY socket 之前，绝不放进 `GATEWAY_CONNECT_TIMEOUT` 的范围。
- `discord_login_attempt` 只在确实要发 IDENTIFY 时写入，这一条和 spec 0005 相同。
- 进程启动本身不算睡眠，第一次采样只建立基准。
- `harvester.collector.discord_client` 仍是唯一导入 `discord` 的文件；`wake_guard.py` 不导入它。
- 时钟差值只会让登录变晚，不会让额度检查放松。

**Security model**: 日志文件可能含 Discord 用户名、频道 ID 和服务器名称，但不能含 token。文件走和 stdout 相同的 `_redact`，`discord` logger 仍固定在 `WARNING`。目录 `0700`，`logs/` 写进 `.gitignore`。`_redact` 只按字段名遮盖，所以新增日志字段只能是枚举值、数字和时间，不放异常消息和响应内容。

**Configuration required**:
- `LOG_DIR`：可选。本地建议写 `logs`；线上不配置，继续用 Docker 的 stdout 日志。`.env.example` 要写明。

**Critical test scenarios**:
- 假时钟：差值 9 秒不算睡眠，11 秒算；第一次采样不算，verifies **AC-1**
- 假时钟：睡眠后 89 秒不放行，90 秒放行；第 45 秒再次睡眠后重新计时，verifies **AC-2**
- 假 ws：IDENTIFY 路径在等待期间没有调用 `ws_factory`，也没有 `discord_login_attempt` 行，verifies **AC-2**
- 假 ws：带 `resume` 的连接在等待期间照常发起，额度行数不变，verifies **AC-3**
- 没有睡眠时，连接路径没有额外等待，verifies **AC-4**
- 等待期间换 token 或取消：等待立刻结束，状态按版本写入规则变化，verifies **AC-5**
- RESUME 被拒并且额度用完：覆盖段的关闭时间仍是断线时间，状态是 `login_paused`，verifies **AC-6**
- 配置 `LOG_DIR` 后写入对应文件；未配置时不创建目录，verifies **AC-7**
- 用很小的 `maxBytes` 验证轮转文件数量上限，verifies **AC-8**
- 把测试 token 写进 `token`、`password`、`authorization` 字段，文件里搜不到；已存在的宽权限目录被改成 `0700`，verifies **AC-9**
- `LOG_DIR` 指向不可写路径时 `configure_logging` 抛异常；重复调用会关闭旧文件 handler，verifies **AC-10**、**AC-7**
- 三种 `reason` 各断言一次，包括新 client 在断线之后的 `after_disconnect`；日志字段里没有 token，verifies **AC-11**

## Build plan

先送出日志，这样后面的保护从第一天起就有证据可查。

**里程碑 1：日志文件**

1. `config.py` 加 `log_dir`，`logging.py` 加文件 handler 和轮转，两个 `__main__.py` 传入进程名，更新 `.env.example` 和 `.gitignore`，satisfies **AC-7**、**AC-8**、**AC-9**、**AC-10**
2. 加诊断日志：断线和失败类型、预留原因、额度用完，包括 `IdentifyRequested.resume_rejected`，satisfies **AC-11**

**里程碑 2：睡眠保护**

3. 新建 `wake_guard.py`：纯函数、`WakeGuard`、`watch_sleep`，在 `collector/main.py` 接上，satisfies **AC-1**、**AC-4**
4. 在 `connect` 的 IDENTIFY 路径前等待，RESUME 路径不等，satisfies **AC-2**、**AC-3**、**AC-5**、**AC-6**

## Consequences

**Positive**:
- 合盖和后台唤醒不再因为短暂的连接消耗额度。
- 每次断线和全新登录都有原因可查。

**Negative / tradeoffs**:
- 打开电脑后如果会话已过期，要多等最多 90 秒才恢复，这期间显示“连接中断，正在重试”，看不出是在等待。这是你选的，换来的是不改前端。
- 这个方案还没用真实合盖验证过。后台唤醒如果超过 90 秒，仍会消耗额度；刚通过等待就又睡着时，也可能损失 1 次额度。
- 退避和 90 秒等待叠加，最坏时要等 5 分钟加 90 秒才发 IDENTIFY。
- 每次真正打开电脑，如果会话已过期，仍会消耗一次额度。一天开合很多次时，额度还是可能用完。

**Neutral**:
- 改动 `collector/` 和 `harvester/logging.py`，按 spec 0001 的发布规则，发布时 collector 和 api 都会重启。
- 暂停时不提供重置额度的按钮。Spec 0005 AC-7 的“不能绕过”保持不变。

## Follow-up

- [ ] `/check verify` 时用真实合盖过夜确认：日志里能看到 `sleep_detected`，后台唤醒期间没有 `identify_reserved`，打开电脑后恢复。如果和这份 spec 不符，就回来修改。
- [ ] 用新日志查清上次第 9、10 次只隔 47 秒的原因；如果和睡眠无关，单独处理。
- [ ] 真实日志出来后，再决定是否还需要“重置登录额度”按钮。
