# 0012. 按时间段采集历史消息

**Date**: 2026-11-04
**Status**: In Progress

## Summary

这个 spec 让 collector 用已经登录的小号，在后台把指定频道、指定时间段的历史消息抓回来，和实时采集的数据存在一起，同一条消息只有一份。你在新的「历史采集」页选频道和时间段建任务，已经采齐的时间默认跳过；频道页会显示断线留下的缺口（没被采集覆盖的时间），可以一键补上，不超过 6 小时的缺口会自动补。因为 selfbot 有封号风险，抓取节奏定得很保守：一次只跑一个任务，每页之间随机等 2 到 4 秒，跑 20 分钟歇 5 分钟，任意 24 小时最多 1500 页，一小时内被限速 3 次就整体暂停，等你手动继续。

## Requirements

**User stories**:
- 作为唯一的使用者，我想选几个频道和一段时间，把那段时间的消息抓回来，这样刚开始采集的频道也能看到以前的内容。
- 作为唯一的使用者，我想看到断线留下的缺口并一键补上，短的缺口自动补，这样数据是连续的。
- 作为唯一的使用者，我想让补采的节奏慢而稳，被限速时自动停下，这样小号不容易被封。
- 作为唯一的使用者，我想随时暂停、继续、取消任务，中断后从断点接着跑，这样大任务不用从头再来。

**Acceptance criteria**:

建任务

- **AC-1**: 「历史采集」页（`/history`）可以新建任务：选一个或多个频道（每次最多 50 个），可选的频道是小号能读（`account_channel_access.can_read` 为真）、状态不是 `deleted`、类型属于 `COLLECTABLE_CHANNEL_TYPES` 的频道，不要求开启实时采集；选起止时间，用浏览器本地时区输入和显示，提供「最近 24 小时」「最近 7 天」「最近 30 天」「全部历史」四个快捷项，也可以自由填写；「全部历史」的起点是这个频道的创建时间（由频道 ID 算出）；结束时间晚于现在时按现在算；起点不早于终点时返回 422 `invalid_range`。可以勾选「整段重抓」，默认不勾。
- **AC-2**: 提交后，重复的频道 ID 先去掉。每个频道的请求起点早于频道创建时间时，截到创建时间。然后先减去这个频道所有未完成任务（`pending`、`running`、`paused`）的时间段；没勾「整段重抓」时再减去这个频道所有覆盖段（`channel_coverage`，实时和历史都算）的并集。剩下的每一段连续时间建一行 `history_job`，同一次提交的所有行共用一个 `batch_id`，`origin = 'manual'`。不建行的频道在结果里记原因，按这个顺序取第一个符合的：库里没有这个频道 `not_found`、`channel_deleted`、`not_collectable`、`no_access`、减完什么都不剩 `nothing_to_fetch`。所有频道都没建行时，页面提示「没有需要采的时间」，说明每个频道的原因。线程是独立的频道：父频道的任务不包含线程里的消息，线程要单独选。

抓取

- **AC-3**: collector 一次只跑一个任务，只在小号在线（`GatewayHandle` 里有会话）且总开关没有暂停时领取。领取顺序：`origin = 'gap_auto'` 的先，其余按 `id` 从小到大。每页最多取 100 条，方向从新到旧：第一页取 `range_end` 之前的消息，之后每页取断点消息之前的消息。每一页请求之前，重新读 `account_channel_access`，这个频道已经不可读时任务按 `no_access` 失败，不发请求。每一页的消息写入（`ingest_message`，`source = 'history'`）、断点和计数更新、限额计数在同一个事务里提交。某一页出现早于 `range_start` 的消息，或者 Discord 返回的原始条数（过滤前，包括不入库的消息类型）不满 100 条，就丢掉早于 `range_start` 的部分，任务变成 `done`，并在同一个事务里写一段历史覆盖 `[range_start, range_end)`。
- **AC-3a**: 任务的第一页（还没有断点时）返回 0 条，而库里这个频道在 `[range_start, range_end)` 对应的 ID 区间内有没删除的消息时，任务按 `suspect_empty` 失败：不写覆盖段，不对账。原因是小号能看到频道但没有「读取消息历史」权限时，Discord 返回空列表而不是报错，这时不能把空结果当成「这段时间没有消息」。
- **AC-4**: 同一条消息已经被实时采集存过，补采时库里仍然只有一行，按 spec 0002 的编辑判断表处理：内容不同就记一次编辑，不丢旧内容；已删除的不改。补采进来的消息，附件按 spec 0011 的规则排队下载，不加例外（所在频道没开采集的附件保持待保存）。

节奏和限额

- **AC-5**: 每页请求之后，随机等待 `page_delay_min_s` 到 `page_delay_max_s` 秒（默认 2 到 4，均匀分布）。连续抓取累计 `run_minutes` 分钟（默认 20）后休息 `rest_minutes` 分钟（默认 5）；队列空着超过 `rest_minutes` 也算休息过，累计清零。任意滚动 24 小时内发出的页数达到 `daily_page_limit`（默认 1500）时不再发请求，直到窗口里的用量降到上限以下。历史任务每次调用都算一页（包括为了拿频道对象而额外调用的 `fetch_channel`）；discord.py-self 内部对 502、504、连接重置的静默重试和 429 后的重发看不到，不计入，所以限额数的是「页」，不是严格的 HTTP 请求数。进入休息或等限额时，手上的任务放回 `pending`，不占着运行位置。
- **AC-6**: 历史采集整体在休息或等限额时，历史页状态条和首页显示原因（「休息中」「今日额度已用完」）和最早可能继续的时间。单个任务因为限速或重试在等时，这一行显示等待原因（`rate_limited`、`retrying`）和预计继续的时间；不在等时这两项为空。
- **AC-7**: 收到 429（限速）时，等 Discord 要求的时间再继续。429 的次数只从 `discord.http` 的限速警告日志里数（discord.py-self 每收到一次 429 都先记一条，包括它自己等待重试的那些，也包括实时会话和附件换链接发出的请求）；`RateLimited` 异常不再重复计数，只用来取等待时间（它在库里要么紧跟着一条已经数过的警告，要么来自本地的预判，并没有真的收到 429）。每次历史请求之前检查：任意 60 分钟内已经有 3 次，总开关自动关闭，原因记为 `rate_limited`，手上的任务回到 `pending`，记一条 `history.paused_rate_limited` 警告日志，这次请求不发。collector 启动时检查 `discord.http` 这个 logger 的有效级别不高于 WARNING 并且没有被禁用，否则记 `error` 日志并把总开关关闭为 `rate_limited`（宁可停，也不在保护失效时继续）。历史页顶部和图标栏的「历史采集」入口显示提示（图标栏是红点，`aria-label` 是「历史采集已因限速暂停」），等你手动继续。实时采集不受影响。
- **AC-8**: 历史页有一个总开关：「暂停全部」和「继续」。暂停时不领取任何任务，包括自动补缺口；正在跑的任务在当前页提交后回到 `pending`。页面显示暂停原因（「你手动暂停」「因限速自动暂停」「账号可能有风险，已自动暂停」）和暂停时间。点「继续」清空原因；collector 在读设置时发现原因从有值变成空，就清空内存里的 429 计数（不依赖 NOTIFY 是否送到）。

任务操作和断点

- **AC-9**: 每个任务可以：暂停（`pending` 或 `running` 变成 `paused`，`running` 的在当前页提交后停）；继续（`paused` 变成 `pending`）；取消（`pending`、`running`、`paused`、`failed` 变成 `cancelled`）；重试（`failed` 变成 `pending`，从断点继续）。每个批次也可以整批暂停、继续、取消，只作用于状态符合的行。状态不符合时单个任务的操作返回 409 `invalid_transition`；整批操作返回实际改了几行。
- **AC-10**: 小号掉线、collector 重启、被自动补缺口任务抢先、总开关暂停时，正在跑的任务回到 `pending`，断点保留，之后从断点继续，最多多抓一页（重复的消息由 `ingest_message` 去重）。被抢先发生在当前页提交之后、下一页开始之前。同一个任务连续被抢先 3 次后，下一次不再让位，跑到它自己完成、失败或被放回为止；它抓到一页之后计数清零（`preempted_count`）。
- **AC-11**: 任务从 `pending`、`running` 或 `paused` 变成 `cancelled` 或 `failed` 时，如果已有断点，就写一段历史覆盖 `[断点消息的时间 + 1 毫秒, range_end)`，在改状态的同一个事务里写；不做删除对账。加 1 毫秒是因为和断点同一毫秒、ID 更小的消息还没抓到。从 `failed` 变成 `cancelled` 不再写（失败时已经写过）。暂停不写覆盖。`suspect_empty` 失败时不写。

失败

- **AC-12**: 请求结果按这张表处理（边界函数 `fetch_history_page` 负责把库的异常换成结果）：

  | discord.py-self 抛出 | 结果 |
  |---|---|
  | `Forbidden`（403） | 任务 `failed`，原因 `no_access` |
  | `NotFound`（404） | 任务 `failed`，原因 `channel_deleted` |
  | `DiscordServerError`（5xx）、`curl_cffi` 的 `requests.RequestsError`、`OSError`、`TimeoutError` | 不算失败，等待原因 `retrying`，从 30 秒起翻倍、封顶 5 分钟；连续 5 次后放回 `pending`，5 分钟内不再领取（`not_before`），让别的任务先跑 |
  | `RateLimited` | 等它给的时间，等待原因 `rate_limited`（不计数，见 AC-7） |
  | `HTTPException` 且状态是 401、消息是 Cloudflare ban，或 `CaptchaRequired` | 总开关关闭，原因 `account_risk`，任务回到 `pending`，记 `history.paused_account_risk` 警告日志 |
  | 其他任何异常 | 任务 `failed`，原因 `unexpected`，记 `error` 日志 |

  任务不改频道本身的状态（那由网关事件负责）。任何任务失败之后，先等一个正常的页间隔再领下一个。提交一页时遇到可重试的数据库错误，也按 `retrying` 退避重发同一页，不算失败。失败的任务只能手动重试。

对账

- **AC-13**: 勾了「整段重抓」的任务，每一页提交时做删除对账：这一页覆盖的消息 ID 区间内，库里有、这一页没返回、还没删除的消息，用 `mark_messages_deleted(..., source='reconciled')` 标记，时间是这一页提交的时间。区间的上界是这一页请求的「之前」位置（第一页是 `range_end` 对应的 ID），下界是这一页最旧的一条消息的 ID；任务的最后一页下界是 `range_start` 对应的 ID；空页的下界也是 `range_start` 对应的 ID。没勾「整段重抓」的任务不对账。

进度

- **AC-14**: 历史页按批次分组显示任务。每行显示：频道（服务器名加频道名，点进去打开 `/channels?id=<频道 ID>`）、时间段、来源（手动、一键补缺口、自动补缺口）、是否整段重抓、状态、进度百分比、已抓条数、当前抓到的时间、等待原因和预计继续时间、失败原因。进度是 `(range_end − 断点消息的时间) / (range_end − range_start)`，没有断点时是 0，`done` 时是 100%。批次标题显示频道数、总条数和各状态的数量，这些合计由接口按整个批次算好返回，不靠页面上已加载的行。「进行中」显示所有未结束的任务和失败的任务；「已结束」默认显示最近 7 天结束的任务，分页加载，可以切换到全部（最多到 30 天的保留期）。任务状态或进度变化后 2 秒内页面更新（SSE 事件 `history_changed`，加 30 秒轮询兜底）。

缺口

- **AC-15**: 一个频道的缺口是：从它最早一段覆盖的起点，到最晚一段覆盖的终点（开放的实时段算到现在）之间，没有被任何覆盖段盖住的时间，不设最短长度（重启留下的几秒缺口里也可能有真的消息）。只算状态不是 `deleted`、小号可读的频道。频道页显示「这个频道有 N 段缺口，共 X」（没有缺口时不显示）和「补缺口」按钮；历史页列出所有有缺口的频道，可以逐个补或者「全部补上」。补缺口建 `origin = 'gap_manual'` 的任务，同样先减去未完成任务的时间段，一段缺口一行。
- **AC-16**: 覆盖对齐给一个频道打开新的实时覆盖段时，如果紧挨在这段前面有一段缺口，并且长度不超过 `auto_gap_max_hours` 小时（默认 6，设为 0 表示关闭自动补），就在对齐的事务提交之后，用一个单独的事务给它建 `origin = 'gap_auto'` 的任务（先减去未完成任务的时间段）。这一步出错只记 `warning` 日志，不影响实时覆盖对齐；漏建的缺口仍然会显示，可以一键补。更长的缺口只在频道页和历史页显示，等你一键补。自动建的任务同样受总开关、节奏和限额约束。

在 dashboard 上的显示

- **AC-17**: 补采写入的消息不发 `new_message` 通知。未读数照 spec 0010 的规则算，不改：比已读位置旧的补采消息不算未读；比已读位置新的（比如你还没读到断线之后的内容，断线期间的消息被补回来）算未读，这正是你还没看过的消息，下一次取未读数时就会显示。每页提交时发一次 `history_changed`。正在看的频道收到属于自己的 `history_changed` 后，5 秒内合并成一次，重新取已经加载的范围，补进来的消息按时间插到正确位置，当前看到的消息不跳动。
- **AC-18**: 没开启实时采集的频道，补采到的消息从任务行点进频道页查看，频道页照常显示已存的消息，顶部是现有的 `collect_off` 提示，另外提供「抓这个频道的历史」按钮。开启采集的频道页也有这个按钮，打开的新建任务对话框里预先选好这个频道。

设置、状态和清理

- **AC-19**: 设置页新增「历史采集」（`/settings/history`），显示并可修改：每页最短间隔和最长间隔（秒，1 到 120，最短不能大于最长）、每 24 小时页数上限（50 到 20000）、连续抓取分钟数（5 到 240）、休息分钟数（1 到 120）、自动补缺口的时长上限（小时，0 到 72，0 表示关闭）。超出范围时不保存并显示错误。保存后几秒内生效，不用重启 collector。页面也显示当前滚动 24 小时的已用页数和上限。
- **AC-20**: 首页系统状态新增「历史采集」一行：历史任务心跳的相对时间；collector 进程心跳正常而历史任务心跳为空或超过 180 秒时，用 error 色调显示「历史采集任务无响应」；总开关暂停时显示暂停原因；正在等待限额时显示「今日额度已用完，最早 <本地时间> 继续」；休息时显示「休息中，<本地时间> 继续」。
- **AC-21**: collector 每小时清理一次：删除 `finished_at` 早于 30 天前的任务行（`done`、`failed`、`cancelled`）；删除 48 小时以前的限额计数行。覆盖段和消息不删。

鉴权

- **AC-22**: 所有新接口都要求已登录的会话，未登录返回 401；所有写操作另外要求 Origin 检查（spec 0004）。

## Decision

**Chosen option**: Option 1：collector 里的历史任务循环，任务表按连续时间段拆行

在 collector 里加一个和附件下载任务并列的历史任务循环，用当前已登录的网关会话发请求；任务表每行是一个频道的一段连续时间，建任务时就用 PostgreSQL 的多段范围运算扣掉已覆盖和已排队的部分；节奏、限额和总开关存在一张单行设置表里，设置页可改。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `shadcn` (`shadcn-ui/ui`, `.claude/skills/shadcn/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`) · `vitest` (`antfu/skills`, `.claude/skills/vitest/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### 代码位置

- `backend/src/harvester/db/history_ranges.py`：纯函数。`snowflake_floor(t) -> int`（某个时间点之前的消息 ID 都小于它：`(毫秒数 − Discord 纪元) << 22`）；`progress(range_start, range_end, cursor_at) -> float`；`next_wait(...)`（给定设置、本次连续抓取的累计时间、滚动用量，算出下一步是立即抓、休息还是等限额，以及等到什么时候）；`reconcile_interval(...)`（AC-13 的 ID 区间）。和已有的 `snowflake_time` 放在一起测试。
- `backend/src/harvester/db/history.py`：数据库函数，都接收显式传入的 `AsyncSession`，都不提交事务。`create_jobs`、`create_gap_jobs`、`claim_next_job`、`commit_page`、`finish_job`、`fail_job`、`release_job`、`set_job_wait`、`transition_job`、`transition_batch`、`list_jobs`、`channel_gaps`、`gap_summary`、`get_history_settings`、`update_history_settings`、`set_history_paused`、`pages_in_window`、`reset_running_on_start`、`cleanup_history`。
- `backend/src/harvester/collector/history_worker.py`：历史任务循环、心跳循环、清理循环，挂在 `collector/main.py` 的任务列表里（名字 `history-worker`、`history-beat`、`history-cleanup`），和 `media-worker` 并列，同样不放进 `waiters`，最外层 `except Exception` 记日志后等 60 秒继续。
- `backend/src/harvester/collector/ratelimit_watch.py`：`RateLimitLog`（一个显式传入的可变容器，和 `GatewayHandle` 同类，不是模块级状态），记录最近 60 分钟的 429 时间；`install_ratelimit_watch(log)` 给 `discord.http` 这个 logger 加一个 `logging.Handler`，只数消息以 `We are being rate limited` 开头的记录（库每收到一次 429 都记且只记一条；同一次 429 后面可能跟着的 `Global rate limit has been hit`、`Cloudflare rate limit has been hit` 不再重复计数）。discord.py-self 收到 429 时先记这条警告再自己等待重试，只有等待时间超过 `max_ratelimit_timeout`（60 秒）才抛 `RateLimited`，所以只靠异常数不全。`ratelimit_watch_healthy() -> bool` 检查这个 logger 的有效级别不高于 WARNING 且没被禁用（项目的 `logging.py` 把 `discord` 固定在 WARNING，现在是满足的），collector 启动时调用（AC-7）。
- `backend/src/harvester/collector/discord_client.py`：新增带类型的边界函数 `fetch_history_page(client, channel_id, before_id) -> HistoryPage`。先用 `client.get_channel`，缓存里没有（比如已归档的线程）再 `fetch_channel` 一次；然后 `channel.history(limit=100, before=discord.Object(before_id), oldest_first=False)`，每条用现有的 `snapshot_message` 转成 `MessageSnapshot`。`HistoryPage` 是判别联合：`Fetched(messages, request_count, returned_ids, raw_count)`、`RateLimited(retry_after)`、`Forbidden`、`NotFound`、`Unavailable(reason)`、`AccountRisk(reason)`、`Unexpected(error_type)`，按 AC-12 的表从异常换过来，边界之外不出现库的异常类型。已核对：`limit=100` 且给了 `before` 时，库走 `_before_strategy`，一页只调用一次 `logs_from`。`GatewaySession` 协议扩出 `fetch_history_page(channel_id, before_id)`。
- `backend/src/harvester/collector/supervisor.py`：`_align` 的对齐事务提交之后，`plan.to_open` 不为空时开一个新事务调用 `create_gap_jobs(session, plan.to_open, at, origin='gap_auto')`（AC-16），外面包 `except Exception` 只记 `history.auto_gap_failed` 警告。`Deps` 新增 `history_wake: asyncio.Event`，由 `history_changed` 的 NOTIFY 和 30 秒轮询设上。
- `backend/src/harvester/api/history_routes.py`：下面的接口；`api/notifications.py` 的映射加上 `history_changed`；`api/system_routes.py` 的系统状态加 `history`。
- 前端：`web/src/app/(dashboard)/history/page.tsx`、`web/src/app/(dashboard)/settings/history/page.tsx`；`web/src/components/history/`（任务列表、批次、新建任务对话框、时间段选择、缺口列表、总开关条）；`web/src/components/shell/` 图标栏新增入口和红点；`web/src/components/channels/` 频道页的缺口提示和两个按钮；`web/src/app/(dashboard)/system-status.tsx` 新增一行；接口调用放 `web/src/lib/api/`。

### 数据模型

**`history_job`**（新表，结构在 spec 0002 预留的基础上加了 `origin`、`refetch`、`pages_fetched`、`wait_reason`、`wait_until`、`not_before`、`preempted_count`）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint identity | 是 | 主键 |
| `batch_id` | uuid | 是 | `uuidv7()`；同一次提交、同一次补缺口拆出来的行共用 |
| `channel_id` | bigint | 是 | 外键 → `channel` |
| `account_id` | bigint | 否 | 外键 → `discord_account`，`ON DELETE SET NULL`；领取时写入 |
| `origin` | text | 是 | `CHECK (origin IN ('manual', 'gap_manual', 'gap_auto'))` |
| `refetch` | boolean | 是 | 默认 `false`；为真时逐页对账（AC-13） |
| `range_start`、`range_end` | timestamptz | 是 | `[)`；`CHECK (range_start < range_end)` |
| `status` | text | 是 | `CHECK (status IN ('pending', 'running', 'paused', 'done', 'failed', 'cancelled'))`，默认 `pending` |
| `cursor_message_id` | bigint | 否 | 断点：已抓到的最旧一条消息 ID |
| `fetched_count` | integer | 是 | 默认 0；`ingest_message` 处理过的条数（不含 `skipped_type`） |
| `pages_fetched` | integer | 是 | 默认 0 |
| `wait_reason` | text | 否 | `CHECK (wait_reason IN ('rate_limited', 'retrying'))`；只在 `running` 时有值（休息和限额不在这里，见 `collector_heartbeat`） |
| `wait_until` | timestamptz | 否 | `CHECK ((wait_reason IS NULL) = (wait_until IS NULL))` |
| `not_before` | timestamptz | 否 | 连续重试 5 次被放回时写入，领取时跳过 `not_before > now()` 的行；领取成功后清空 |
| `preempted_count` | smallint | 是 | 默认 0；被 `gap_auto` 抢先一次加 1，抓到一页后清零（AC-10） |
| `error` | text | 否 | `CHECK (error IN ('no_access', 'channel_deleted', 'suspect_empty', 'unexpected'))`；只在 `failed` 时有值 |
| `created_at`、`updated_at` | timestamptz | 是 | 默认 `now()` |
| `started_at` | timestamptz | 否 | 第一次领取的时间 |
| `finished_at` | timestamptz | 否 | 进入 `done`、`failed`、`cancelled` 的时间；重试时清空 |

索引（都写明名字）：
- `ix_history_job_claim`：`((origin <> 'gap_auto'), id) WHERE status = 'pending'`，领取用。
- `ix_history_job_channel_open`：`(channel_id) WHERE status IN ('pending', 'running', 'paused')`，建任务时扣掉未完成任务用。
- `ix_history_job_batch`：`(batch_id)`。
- `ix_history_job_finished`：`(finished_at) WHERE finished_at IS NOT NULL`，清理和「已结束」分页用。
- 部分唯一索引 `uq_history_job_running`：`((true)) WHERE status = 'running'`，保证同时最多一个任务在跑。

**`history_settings`**（新表，单行，和 `media_settings` 同一个做法）

| 字段 | 类型 | 默认 | 约束 |
|---|---|---|---|
| `id` | smallint | 1 | `CHECK (id = 1)` |
| `page_delay_min_s` | smallint | 2 | 1 到 120 |
| `page_delay_max_s` | smallint | 4 | 1 到 120，`CHECK (page_delay_min_s <= page_delay_max_s)` |
| `daily_page_limit` | integer | 1500 | 50 到 20000 |
| `run_minutes` | smallint | 20 | 5 到 240 |
| `rest_minutes` | smallint | 5 | 1 到 120 |
| `auto_gap_max_hours` | smallint | 6 | 0 到 72 |
| `paused_reason` | text | 空 | `CHECK (paused_reason IN ('manual', 'rate_limited', 'account_risk'))` |
| `paused_at` | timestamptz | 空 | `CHECK ((paused_reason IS NULL) = (paused_at IS NULL))` |
| `updated_at` | timestamptz | `now()` | |

迁移时插入这一行。

**`history_page_hour`**（新表，滚动 24 小时限额）

| 字段 | 类型 | 说明 |
|---|---|---|
| `hour` | timestamptz 主键 | `date_trunc('hour', now())` |
| `pages` | integer | `CHECK (pages >= 0)`；`commit_page` 里 `INSERT ... ON CONFLICT (hour) DO UPDATE SET pages = pages + EXCLUDED.pages` |

当前用量是 `hour > now() − interval '24 hours'` 的行的 `pages` 之和。按整点分桶，所以一页最多会在窗口里多留不到 1 小时，偏保守。

最早可能继续的时间（`quota_resumes_at`）：把窗口里的桶按 `hour` 从早到晚排，依次从用量里减掉每一桶；第一次让剩下的用量低于上限的那一桶，它的 `hour + 24 小时 + 1 小时` 就是答案。这是纯函数 `quota_resumes_at(buckets, limit)`，在 `history_ranges.py` 里。

**`collector_heartbeat`**（已有，新增三列）：`history_worker_seen_at timestamptz NULL`，历史任务心跳循环每 30 秒写一次；`history_waiting_reason text NULL`（`CHECK (history_waiting_reason IN ('rest', 'quota'))`）和 `history_waiting_until timestamptz NULL`（`CHECK ((history_waiting_reason IS NULL) = (history_waiting_until IS NULL))`），历史采集整体休息或等限额时写入，结束时清空（AC-6）。

**`channel_coverage`**：不改表。历史任务写入 `source = 'history'` 的段（AC-3、AC-11）。

**关系**：`channel 1 ─ N history_job`；`discord_account 1 ─ N history_job`（可空）；`history_settings`、`history_page_hour` 独立。

### 状态变化

```
             领取（AC-3）
  pending ───────────────▶ running ──(抓完)──▶ done
    ▲  ▲                    │  │  │
    │  └─(掉线、重启、被抢先、总开关暂停，AC-10)─┘  │  └─(403、404、空页可疑、意外，AC-12)─▶ failed
    │                       │                            │
    │      (你暂停，AC-9)    ▼                            │
    └──(继续)──────────── paused                          │
    └──(重试)────────────────────────────────────────────┘
  pending、running、paused、failed ──(取消)──▶ cancelled
```

- `done`、`cancelled` 是终态。
- 进入 `running`：写 `account_id`、第一次时写 `started_at`。离开 `running`：清空 `wait_reason`、`wait_until`。
- 进入 `done`、`failed`、`cancelled`：写 `finished_at`；`failed` 写 `error`。`failed → pending`：清空 `error`、`finished_at`，断点保留。
- **并发规则**：api 改任务状态（暂停、继续、取消、重试，单个和整批）时都先 `SELECT ... FOR UPDATE` 锁住要改的行（整批按 `id` 排序加锁），和 collector 的写入互相串行。collector 对任务行的每一次写入（`commit_page` 之外的 `set_job_wait`、`release_job`、`fail_job`、`finish_job`）都带 `WHERE id = :id AND status = 'running'`，并检查影响行数：是 0 行说明 api 刚改过状态，collector 不再碰这一行，放下任务回到第 1 步。所以 collector 永远不会把你取消或暂停的任务改回去。
- `commit_page` 锁住任务行后看状态：不是 `done`、`failed`、`cancelled` 时（包括你刚暂停或暂停后又继续成 `pending` 的情况），照常写消息并更新断点和计数；是 `cancelled` 或 `failed` 时，消息照样写（都是真实数据，丢掉反而要重抓），但不动这一行，也不写覆盖段。是 `done` 不会发生（只有 collector 自己写 `done`）。
- collector 启动时（`_startup`）调用 `reset_running_on_start`：把所有 `running` 改回 `pending`，清空等待字段，清空 `collector_heartbeat` 的两个等待列（spec 0002 原来写的是「自己账号下的」，现在只有一个 collector，全部改回）。

### 历史任务循环（`history_worker`）

每一轮：

1. 读 `history_settings`。`paused_reason` 从有值变成空时，清空 `RateLimitLog`（AC-8）。`paused_reason` 不为空，或者 `GatewayHandle` 里没有会话：手上有任务就 `release_job`，然后等 `history_wake`（最长 30 秒）回到第 1 步。
2. 算整体等待（纯函数 `next_wait`，输入是设置、本次连续抓取累计时间、空闲开始时间、窗口里的桶）：累计达到 `run_minutes` → 休息到 `now + rest_minutes`；滚动用量达到 `daily_page_limit` → 等到 `quota_resumes_at`。需要等时：手上有任务就 `release_job`（放回 `pending`，不占运行位置），写 `collector_heartbeat.history_waiting_reason` 和 `history_waiting_until`，等到那个时间（期间被 `history_wake` 唤醒就回到第 1 步重新检查，所以暂停、改设置都能及时生效），等完清空这两列。休息结束时累计清零。
3. 手上没有任务时 `claim_next_job`：`SELECT ... WHERE status = 'pending' AND (not_before IS NULL OR not_before <= now()) ORDER BY (origin <> 'gap_auto'), id LIMIT 1 FOR UPDATE SKIP LOCKED`，改成 `running`，清空 `not_before`。没有任务就等 `history_wake`（最长 30 秒），并记下空闲开始的时间（AC-5 的「空着超过休息时间也算休息过」）。
4. 请求之前的检查：`RateLimitLog` 里 60 分钟内已经有 3 次 → `set_history_paused('rate_limited')`，`release_job`，记 `history.paused_rate_limited`，回到第 1 步；`account_channel_access` 显示这个频道已经不可读 → `fail_job('no_access')`。
5. 发一页：`before_id` 是断点，没有断点时是 `snowflake_floor(range_end)`。按结果处理：
   - `Fetched`：第一页（没有断点）、`raw_count = 0`、而库里这个频道在 `[snowflake_floor(range_start), snowflake_floor(range_end))` 内有没删除的消息 → `fail_job('suspect_empty')`，不写覆盖段（AC-3a）。否则开事务，`SELECT ... FOR UPDATE` 锁住任务行；按时间从新到旧逐条 `ingest_message`（写入顺序同 spec 0002，`source = 'history'`，`received_at` 是这一页收到的时间），丢掉早于 `range_start` 的；`refetch` 为真时按 AC-13 对账；按上面的并发规则更新 `cursor_message_id`（这一页里不早于 `range_start` 的最旧一条）、`fetched_count`、`pages_fetched`，`preempted_count` 清零；`history_page_hour` 加 `request_count`；`pg_notify('history_changed', {job_id, channel_id})`。到底了（AC-3，用 `raw_count` 判断是否满 100 条）就同一个事务里 `done` 并写覆盖段。提交。重试计数清零。
   - `RateLimited(retry_after)`：不计数（AC-7），`set_job_wait('rate_limited', now + retry_after)`，等待后重发同一页。
   - `Forbidden`、`NotFound`：`fail_job`（AC-11 的覆盖段、AC-12 的原因）。
   - `Unavailable`：`set_job_wait('retrying', ...)`，30 秒起翻倍、封顶 5 分钟；连续第 5 次时改为 `release_job` 并写 `not_before = now + 5 分钟`。
   - `AccountRisk`：`set_history_paused('account_risk')`，`release_job`，记 `history.paused_account_risk`。
   - `Unexpected`：`fail_job('unexpected')`，记 `error` 日志。
6. 提交一页或任务结束之后：状态不再是 `running`（影响行数为 0）→ 放下这个任务；当前任务不是 `gap_auto`、`preempted_count < 3`、并且存在可领取的 `pending` `gap_auto` → `release_job` 让位并把 `preempted_count` 加 1（AC-10）；`GatewayHandle` 空了 → `release_job`。然后随机等 `page_delay_min_s` 到 `page_delay_max_s` 秒（任务失败之后也等，AC-12），累计连续抓取时间，回到第 1 步。

`commit_page` 遇到可重试的数据库错误（`is_transient_db_error`）时整页回滚，按 `retrying` 退避后重发同一页，和 `Unavailable` 共用重试计数（AC-12）。

### 范围运算

都在 SQL 里用 `tstzmultirange` 做，结果用 `unnest` 拆成连续段：

- 建任务（AC-2）：`tstzmultirange(tstzrange(start, end)) − 未完成任务的 range_agg − （不勾整段重抓时）覆盖段的 range_agg`，空集合用 `'{}'::tstzmultirange` 代替。建任务、补缺口、自动补都在一个事务里先 `SELECT ... FROM channel WHERE id = ANY(...) ORDER BY id FOR UPDATE` 锁住频道行，避免两次并发提交排出重叠的任务；固定按 `id` 排序加锁，避免互相等待造成死锁。
- 缺口（AC-15）：只算 `status <> 'deleted'` 且小号可读的频道。`m = range_agg(span)`；`tstzmultirange(tstzrange(lower(m), coalesce(upper(m), now()))) − m`。开放的实时段上界是无穷，算到 `now()`。
- 自动补（AC-16）：对 `plan.to_open` 里的每个频道，取上界等于新实时段起点的那一段缺口；长度大于 0 且不超过 `auto_gap_max_hours` 时，减去未完成任务后建行。`auto_gap_max_hours = 0` 时整个步骤跳过。

### 接口

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/history/jobs` | GET | `view`：`active` 或 `finished`（查询，默认 `active`）；`since_days`：int（查询，1 到 30，默认 7，只用于 `finished`）；`before_id`：str（查询，`finished` 分页用）；`limit`：int（1 到 100，默认 50） | `HistoryJobPage`：`jobs`、`batches`、`next_before_id` | 会话 | 401；422 |
| `/api/history/jobs` | POST | `channel_ids`：str 列表（1 到 50）；`range_start`：带时区的时间或 `null`（`null` 表示全部历史）；`range_end`：带时区的时间或 `null`（`null` 表示现在）；`refetch`：bool（默认 false） | `CreateJobsOut`：`batch_id`（没建行时为空）、`jobs`、`skipped`（`channel_id`、`reason`） | 会话加 Origin | 422 `invalid_range`（起点不早于终点，或时间没带时区）、`too_many_channels` |
| `/api/history/jobs/{job_id}/{action}` | POST | `action`：`pause`、`resume`、`cancel`、`retry` | `HistoryJobOut` | 会话加 Origin | 404 `job_not_found`；409 `invalid_transition` |
| `/api/history/batches/{batch_id}/{action}` | POST | `action`：`pause`、`resume`、`cancel` | `{ "changed": int }` | 会话加 Origin | 404 `batch_not_found` |
| `/api/history/gaps` | GET | `channel_id`：str（查询，可选；不给就返回所有有缺口的频道） | `GapSummaryOut` 列表：`channel_id`、`guild_name`、`channel_name`、`gaps`（`start`、`end`）、`total_seconds` | 会话 | 401 |
| `/api/history/gaps/fill` | POST | `channel_ids`：str 列表（1 到 200） | `CreateJobsOut` | 会话加 Origin | 422 |
| `/api/history/control` | POST | `paused`：bool | `HistoryStatusOut` | 会话加 Origin | 401 |
| `/api/history/settings` | GET | 无 | `HistorySettingsOut` | 会话 | 401 |
| `/api/history/settings` | PUT | 六个数值（见 AC-19） | `HistorySettingsOut` | 会话加 Origin | 422 超出范围 |
| `/api/system/status`（已有） | GET | 不变 | 新增 `history: HistoryStatusOut` | 不变 | 不变 |
| `/api/events`（已有 SSE） | GET | 不变 | 新增事件 `history_changed`：`{ "job_id": str, "channel_id": str }` | 不变 | 不变 |

- 所有 ID 在 JSON 里都是字符串（同 spec 0008，避免浏览器丢精度）。
- `HistoryJobOut`：`id`、`batch_id`、`channel_id`、`guild_name`、`channel_name`、`origin`、`refetch`、`range_start`、`range_end`、`status`、`fetched_count`、`pages_fetched`、`cursor_at`、`progress`、`wait_reason`、`wait_until`、`not_before`、`error`、`created_at`、`started_at`、`finished_at`。
- `HistoryBatchOut`（`batches` 里每个批次一项，覆盖这一页 `jobs` 里出现的所有 `batch_id`）：`batch_id`、`channel_count`、`fetched_count`（整个批次的合计）、`status_counts`（六个状态各一个整数），都按整个批次在数据库里算，不受分页影响（AC-14）。
- `active` 返回所有 `pending`、`running`、`paused`、`failed` 的行（`failed` 留在这里，等你重试或取消），按 `batch_id` 再按 `id` 排序，不分页（同时未结束的行数受建任务的上限约束，实际很少）；`finished` 返回 `since_days` 天内结束的 `done` 和 `cancelled`，按 `finished_at` 倒序分页。
- `HistoryStatusOut`：`paused_reason`、`paused_at`、`waiting_reason`、`waiting_until`（`collector_heartbeat` 的两个等待列，已过期按空返回）、`pages_used_24h`、`daily_page_limit`、`quota_resumes_at`（用量达到上限时按 `quota_resumes_at(buckets, limit)` 算；否则为空）、`last_seen_at`（`history_worker_seen_at`）、`stale`（进程心跳正常而 `last_seen_at` 为空或超过 180 秒）、`gap_channel_count`。
- `HistorySettingsOut`：六个数值，加 `pages_used_24h`。
- `POST /api/history/control` 设 `paused = false` 时清空 `paused_reason`、`paused_at` 并 `pg_notify('history_changed', '{}')` 唤醒 collector（清空 `RateLimitLog` 由 collector 读设置时自己判断，见循环第 1 步）；设 `paused = true` 时写 `manual`（已经是 `rate_limited` 或 `account_risk` 时保留原因不覆盖）。
- 写操作改动任务状态、设置或总开关时，都在同一个事务里 `pg_notify('history_changed', ...)`，collector 和页面都靠它及时响应。

### 前端

- 图标栏新增「历史采集」入口（`/history`），`history.paused_reason = 'rate_limited'` 时显示红点。
- `/history` 页从上到下：状态条（总开关、暂停原因、24 小时用量和上限、额度用完时的预计继续时间、无响应提示）；「新建任务」按钮；「进行中」按批次分组的任务列表（每行有进度条和操作按钮，批次标题有整批操作）；「缺口」列表（有缺口的频道、段数、总时长、「补缺口」，标题上「全部补上」）；「已结束」列表（分页加载）。
- 新建任务对话框：频道多选（按服务器分组、可搜索，数据来自现有的频道列表接口，只列 AC-1 允许的频道），时间段快捷项和两个本地时间输入，「整段重抓」勾选框，提交后显示建了几个任务、哪些频道被跳过和原因。
- 频道页：缺口提示一行和「补缺口」按钮；「抓这个频道的历史」按钮；收到当前频道的 `history_changed` 时，5 秒合并一次，重新取已加载的范围，沿用 spec 0003 的「加载更早不跳动」锚点逻辑保持当前位置。
- `/settings/history`：六个数字输入和保存按钮、24 小时用量。
- 首页系统状态新增「历史采集」一行（AC-20）。
- 接口错误走现有的统一错误转换。`history_changed` 在历史页 1 秒合并一次后重新取任务列表和状态；SSE 重连和每 30 秒也重新取。

### Value sourcing

| 动作 | 产生或显示的值 | 来源 |
|---|---|---|
| 建任务 | 「全部历史」的起点 | `snowflake_time(channel.id)`（频道创建时间） |
| 建任务 | 结束时间 | 请求里的 `range_end`；为空或晚于现在时用数据库 `now()` |
| 建任务 | 用户输入的本地时间 | 浏览器把 `datetime-local` 按本地时区转成带时区的 ISO 时间再提交；api 存 UTC |
| 建任务 | 能否建 | `account_channel_access.can_read`（当前唯一账号）、`channel.status`、`channel.type` |
| 建任务 | 要扣掉的时间 | `channel_coverage.span` 的并集、未完成 `history_job` 的 `[range_start, range_end)` 并集 |
| 建任务 | `batch_id` | 数据库 `uuidv7()` |
| 领取 | 先后顺序 | `origin`、`id` |
| 领取 | `account_id` | `GatewayHandle` 当前会话对应的账号（监督循环的 `AccountRow.id`） |
| 抓一页 | `before_id` | `cursor_message_id`；为空时 `snowflake_floor(range_end)` |
| 抓一页 | 是否到底 | 这一页有早于 `range_start` 的消息（由 `snowflake_time(id)` 判断），或者 `Fetched.raw_count`（过滤前的条数）不满 100 |
| 抓一页 | `received_at` | collector 收到这一页的时间（`deps.clock()`） |
| 抓一页 | 这一页算几页 | `Fetched.request_count`（含 `fetch_channel`） |
| 对账 | ID 区间 | AC-13：上界是请求的 `before_id`，下界是这一页最旧一条的 ID，或者 `snowflake_floor(range_start)` |
| 对账 | `deleted_at` | 这一页提交时的数据库 `now()` |
| 等待 | `history_waiting_until`（休息） | 开始休息的时间加 `history_settings.rest_minutes` |
| 等待 | `history_waiting_until`（限额）、`quota_resumes_at` | `quota_resumes_at(buckets, limit)`：按桶从早到晚累减，第一次低于上限的那一桶的 `hour` 加 25 小时 |
| 重试 | `not_before` | 连续第 5 次重试时的时间加 5 分钟 |
| 抢先 | 还能不能让位 | `history_job.preempted_count < 3` |
| 请求前 | 频道还能不能读 | `account_channel_access.can_read`（当前账号） |
| 空页判断 | 库里区间内有没有消息 | `message` 表 `channel_id` 相同、`id` 在 `[snowflake_floor(range_start), snowflake_floor(range_end))` 内、`deleted_at IS NULL` 的行是否存在 |
| 等待 | `wait_until`（限速） | Discord 返回的 `retry_after` |
| 等待 | 每页间隔 | `random.uniform(page_delay_min_s, page_delay_max_s)`，随机数来源作为参数传进 `history_worker`，测试里固定 |
| 自动暂停 | 429 次数 | `RateLimitLog`：只来自 `discord.http` 日志钩子的 `We are being rate limited` 记录 |
| 自动暂停 | 账号风险 | `fetch_history_page` 返回的 `AccountRisk`（401、Cloudflare ban、`CaptchaRequired`） |
| 覆盖段 | `done` 时的范围 | `[range_start, range_end)` |
| 覆盖段 | 取消或失败时的范围 | `[snowflake_time(cursor_message_id) + 1 毫秒, range_end)` |
| 进度 | `cursor_at` | `snowflake_time(cursor_message_id)` |
| 进度 | `progress` | `(range_end − cursor_at) / (range_end − range_start)`；没有断点为 0，`done` 为 1 |
| 缺口 | 缺口段 | AC-15 的 `range_agg` 运算，开放段算到数据库 `now()` |
| 自动补 | 时长上限 | `history_settings.auto_gap_max_hours` |
| 自动补 | 新实时段起点 | `align_coverage` 用的 `at`（spec 0006） |
| 状态 | `pages_used_24h` | `history_page_hour` 最近 24 小时的和 |
| 状态 | `last_seen_at`、`stale` | `collector_heartbeat.history_worker_seen_at`、`last_seen_at` |
| 频道页 | 频道名、服务器名 | `channel.name`、`guild.name` |
| 显示 | 本地时间 | 浏览器时区格式化 |

### Key invariants

- 同时最多一个 `running` 的任务（部分唯一索引）。
- collector 只改 `status = 'running'` 的任务行；api 改过的状态 collector 不会改回去（带条件的写入，检查影响行数）。
- 休息和等限额时没有任务占着 `running`。
- 同一个频道的未完成任务时间段互不重叠（建任务和补缺口都在锁住频道行后扣掉已有的未完成任务）。
- 断点只往更旧的方向走：新的 `cursor_message_id` 一定小于旧值；`cursor_message_id` 对应的时间不早于 `range_start`。
- 一页的消息、断点、计数、限额计数和对账在同一个事务里，要么都写入要么都不写。
- 补采只通过 `ingest_message` 写消息，所以去重、编辑判断、附件排队和实时采集完全一致。
- 补采不发 `new_message`。
- 删除对账只发生在勾了「整段重抓」的任务上，只针对已经拿到的完整一页的 ID 区间（这一条取代 spec 0002 原来的「只在 `done` 时对账」）。
- 总开关暂停时不发出任何历史请求。
- 空的第一页而库里区间内有消息时，不写覆盖段也不对账。
- 同一段失败或取消只写一次部分覆盖段。
- `history_job` 只有清理会删除，只删结束 30 天以上的行（这是 spec 0002「只允许删除空覆盖段」之外新增的例外）。

### Security model

- 单用户：所有接口要求 dashboard 会话；写操作另外要求 Origin 检查（spec 0004 的现有依赖）。
- 历史请求只由 collector 用已登录的小号发出；api 不连 Discord，也不接触 token。
- 只能对小号有读取权限的频道建任务，不尝试读取无权访问的内容。
- 不涉及受监管的数据类别；主要风险是 Discord 封号，靠 AC-5 到 AC-8 的节奏、限额和自动暂停控制。

### Configuration required

不新增环境变量和密钥。节奏和限额存在 `history_settings` 里，设置页可改。

### 日志

`history.job_claimed`、`history.job_done`（`fetched_count`、`pages_fetched`）、`history.job_failed`（`error`、HTTP 状态）、`history.job_released`（原因：`preempted`、`offline`、`paused`、`shutdown`）、`history.waiting`（`wait_reason`、`wait_until`）、`history.rate_limited`（`retry_after`、窗口内次数）、`history.paused_rate_limited`（warning）、`history.paused_account_risk`（warning，带原因）、`history.ratelimit_watch_unhealthy`（error）、`history.auto_gap_jobs_created`（频道数、行数）、`history.auto_gap_failed`（warning）、`history.cleanup`（删除行数）。不记消息内容。

### Critical test scenarios

- 正常流程：建一个 3 页的任务，假的网关会话依次返回 100、100、40 条，结果 240 条入库，`source = 'history'`，任务 `done`，写入 `[range_start, range_end)` 的历史覆盖段，每页一次 `history_changed`，没有 `new_message`，verifies **AC-3**、**AC-17**
- 去重：同一条消息先实时入库再被补采，只有一行，`source` 仍是 `realtime`；补采快照内容不同时多一个版本，verifies **AC-4**
- 拆分：请求 `[0点, 24点)`，已有实时覆盖 `[6点, 12点)` 和未完成任务 `[18点, 20点)`，建出 `[0点, 6点)`、`[12点, 18点)`、`[20点, 24点)` 三行，同一个 `batch_id`；勾整段重抓时只扣掉 `[18点, 20点)`；全部被扣完时返回 `nothing_to_fetch`；两个并发提交不会排出重叠的行，verifies **AC-2**
- 节奏：固定随机数和假时钟，验证每页间隔、20 分钟后休息 5 分钟、空闲超过 5 分钟后累计清零、滚动用量到上限后 `wait_reason = 'quota'` 且 `wait_until` 正确，verifies **AC-5**、**AC-6**
- 限速：日志钩子在 60 分钟内记到 3 次 429 后，下一次请求之前总开关变成 `rate_limited`，任务回到 `pending`，之后不再领取；两次 429 不暂停；一次 `RateLimited` 异常加它前面那条警告只算 1 次；库的预判 `RateLimited`（没有警告）算 0 次；「继续」后（不发 NOTIFY 也行）计数清零；把 `discord.http` 的级别设成 ERROR 后启动，总开关被关闭，verifies **AC-7**、**AC-8**
- 账号风险：边界函数收到 401、Cloudflare ban、`CaptchaRequired` 时总开关变成 `account_risk`，任务回到 `pending` 且不是 `failed`；用一个固定的当前版本限速警告文本断言钩子能识别，verifies **AC-7**、**AC-12**
- 空页：第一页返回 0 条而库里区间内有未删除的消息 → `failed`，原因 `suspect_empty`，没有新覆盖段，消息一条都没被标删除（勾了整段重抓也一样）；库里区间内没有消息时空页正常 `done`；请求前发现 `can_read` 为假直接 `no_access` 且没有发请求，verifies **AC-3**、**AC-3a**
- 抢先和断点：手动任务跑了 2 页时出现 `gap_auto` 任务，手动任务回到 `pending` 且断点不变，`gap_auto` 先跑完，然后手动任务从断点继续；连续被抢先 3 次后第 4 次不让位，verifies **AC-3**、**AC-10**
- 休息和限额：进入休息或等限额时任务回到 `pending`、没有 `running` 行，`collector_heartbeat` 写了等待原因；`quota_resumes_at` 用几组桶验证（最早一桶释放后仍超上限时取下一桶），verifies **AC-5**、**AC-6**、**AC-20**
- 并发：api 在 collector 写入之前取消 `running` 的任务，collector 随后的 `set_job_wait`、`release_job`、`fail_job` 影响 0 行，状态仍是 `cancelled`；`commit_page` 在 `cancelled` 时写了消息但没改断点，verifies **AC-9**
- 掉线和重启：会话句柄清空后任务回到 `pending`；`reset_running_on_start` 把 `running` 改回 `pending`，verifies **AC-10**
- 状态操作：每个合法转换成功，非法的返回 409；暂停 `running` 的任务时正在提交的那一页仍然写入、断点更新，之后不再请求；整批操作只改符合的行，verifies **AC-9**
- 半途结束：取消一个有断点的任务写入 `[断点时间 + 1 毫秒, range_end)`；没有断点时不写；失败同理；失败后再取消不再写第二段；都不对账，verifies **AC-11**
- 失败：403 和 404 分别变成 `no_access`、`channel_deleted`，频道状态不变，等一个页间隔后下一个任务继续；5xx 和 `requests.RequestsError` 变成 `retrying` 并退避，成功后清零，连续 5 次后放回 `pending` 并带 `not_before`，期间先跑别的任务；数据库瞬时错误重发同一页不失败，verifies **AC-12**
- 对账：整段重抓的一页返回 ID 为 10、8、7 的消息，库里同一区间还有未删除的 9，9 被标成 `reconciled`；区间外的消息不动；不勾整段重抓时不标，verifies **AC-13**
- 缺口：覆盖 `[1点, 3点)`、`[5点, ∞)` 的频道，缺口是 `[3点, 5点)`；只有一段开放覆盖的频道没有缺口；已删除或不可读的频道不列出；对齐打开新段后，紧挨着的 2 小时缺口和 5 秒缺口都自动建 `gap_auto`，10 小时的不建，`auto_gap_max_hours = 0` 时都不建；`create_gap_jobs` 抛异常时实时覆盖段照常打开，verifies **AC-15**、**AC-16**
- 建任务细节：重复的频道 ID 只建一次；不存在的 ID 记 `not_found`；起点早于频道创建时间时被截断；不带时区的时间返回 422；批次合计在分页之后仍是整个批次的数，verifies **AC-2**、**AC-14**
- 进度：`range` 是 10 小时、断点在终点前 4 小时，`progress = 0.4`，verifies **AC-14**
- 设置：超出范围的值返回 422 不保存；最短间隔大于最长间隔返回 422；保存后 collector 下一页就用新值，verifies **AC-19**
- 清理：31 天前结束的任务被删除，29 天前的保留，未结束的不论多旧都保留；49 小时前的限额行被删除，verifies **AC-21**
- 鉴权：未登录访问每个新接口返回 401；写接口缺少或不匹配 Origin 时被拒绝，verifies **AC-22**
- 前端：新建任务对话框的快捷项按本地时区算出正确的起止时间；任务行渲染各状态、进度和等待原因；频道页收到 `history_changed` 后合并取数且不跳动；红点和 `aria-label`，verifies **AC-1**、**AC-7**、**AC-14**、**AC-17**、**AC-18**、**AC-20**

## Build plan

按 Tracer Bullet：先打通「建任务 → collector 抓 → 入库 → 页面看到进度和消息」这条细线，再逐段加粗节奏和安全、操作和失败、缺口。

1. 打通细线：一次迁移建 `history_job`、`history_settings`（插入默认行）、`history_page_hour`，给 `collector_heartbeat` 加 `history_worker_seen_at`；`history_ranges.py` 的 `snowflake_floor` 和 `progress`；`create_jobs`（含多段范围扣减和频道行锁）、`claim_next_job`、`commit_page`、`finish_job`；`fetch_history_page` 边界函数和 `GatewaySession` 协议扩展；`history_worker` 只做领取、按固定间隔抓页、到底 `done` 并写覆盖段；`history_changed` 通知和 SSE 映射；`GET`、`POST /api/history/jobs`；最小的 `/history` 页（新建任务对话框和进行中列表）和图标栏入口，satisfies **AC-1**、**AC-2**、**AC-3**、**AC-3a**、**AC-4**、**AC-14**、**AC-22**
2. 节奏和安全：`next_wait`、`quota_resumes_at` 纯函数和随机间隔、休息、滚动限额，休息和等限额时放回任务；`RateLimitLog`、`discord.http` 日志钩子和启动自检；总开关、`POST /api/history/control`、限速和账号风险两种自动暂停；抢先、掉线和重启时的 `release_job`、`reset_running_on_start`；`set_job_wait` 和等待原因显示；历史任务心跳；`/settings/history` 和两个设置接口；系统状态新增 `history` 和首页一行、图标栏红点，satisfies **AC-5**、**AC-6**、**AC-7**、**AC-8**、**AC-10**、**AC-19**、**AC-20**
3. 操作和失败：单个和整批的暂停、继续、取消、重试接口和按钮；collector 写入的状态条件；取消和失败时写部分覆盖段；AC-12 的异常映射表、重试上限和 `not_before`；数据库瞬时错误重发；整段重抓的逐页对账；`finished` 列表分页；清理循环，satisfies **AC-9**、**AC-11**、**AC-12**、**AC-13**、**AC-14**、**AC-21**
4. 缺口：`channel_gaps`、`gap_summary`、`create_gap_jobs`；`GET /api/history/gaps`、`POST /api/history/gaps/fill`；`_align` 提交后用单独事务自动建 `gap_auto`；频道页的缺口提示、「补缺口」和「抓这个频道的历史」按钮；频道页收到 `history_changed` 后合并重取，satisfies **AC-15**、**AC-16**、**AC-17**、**AC-18**
5. 测试和收口：上面 Critical test scenarios 里的后端集成测试（连真实 PostgreSQL，假的网关会话和假时钟）、前端渲染测试；OpenAPI 导出和 `pnpm gen:api` 同步；更新 spec 0002 里被这个 spec 改动的三处（见 Follow-up）；各项检查，satisfies **AC-1** 到 **AC-22**

## Consequences

**Positive**:
- 刚开始采集的频道也能拿到以前的消息，断线留下的缺口能看见、能补，短的自动补上，数据接近连续。
- 补采完全走现有的 `ingest_message`，去重、编辑留痕和附件下载不用写第二套。
- 每行是一段连续时间，断点、进度、部分覆盖都只有一个数字要管，重启和抢先都很简单。
- 节奏、限额和总开关集中在一处，可以在设置页随时调，不用重启。

**Negative / tradeoffs**:
- 按默认值，一天最多大约 15 万条，「全部历史」对大频道可能要跑好几天到几周。这是为了降低封号风险主动接受的。
- 一个 6 小时以内的断线就会产生一个自动任务，频繁断线时会有很多只抓一两页的小任务（每个都占限额）。缺口不设最短长度：collector 每重启一次，每个在采集的频道都会多一个几秒长的缺口，各花大约 1 页。这是有意的，那几秒里的消息是真的漏掉了。
- 限额数的是「页」，discord.py-self 内部的静默重试不计入，实际 HTTP 请求数会比显示的略多。
- 429 计数是整个 collector 进程的：实时会话或附件换链接被限速，也会让历史采集暂停。这是有意的（任何限速都说明账号在被盯着），但可能让你多点几次「继续」。
- 429 计数靠识别 discord.py-self 的日志文字；库升级改了措辞就会数漏。升级依赖时要检查（见 Follow-up）。
- 补采的旧消息附件链接大多已过期，会让附件任务的换链接量明显上升，受 spec 0011 的每小时 600 个限制，补完要更久。
- 「整段重抓」对一大段已覆盖的时间会重复发很多请求，所以默认不勾。
- 频道页收到补采通知时会重新取已加载的范围，大任务进行中看那个频道会多一些查询（5 秒最多一次）。
- 自己关掉开关的那段时间也算缺口（你选的定义），所以关过再开的频道会显示一段可能很长的缺口；超过 6 小时不会自动补。

**Neutral**:
- `history_job` 成了第一张会被定期删除的业务表，spec 0002 的「不删除」不变量要加一个例外。
- spec 0002 的删除对账规则从「只在 `done` 时」改成「整段重抓任务的每一个完整页」。
- 历史任务心跳和附件任务心跳一样挂在 `collector_heartbeat` 上。

## Follow-up

- [ ] 更新 spec 0002：`history_job` 的字段按本 spec；删除对账规则改成本 spec 的 AC-13；「不删除」不变量加上「结束 30 天以上的 `history_job`」；`history_job.status` 的转换以本 spec 为准。
- [ ] 升级 `discord.py-self` 时检查 `discord/http.py` 里限速警告的措辞是否变化，`ratelimit_watch.py` 的匹配要跟着改；加一个测试直接断言当前版本的措辞。
- [ ] 上线一段时间后看一下 Lightsail 的 CPU 积分（spec 0001、0009 的提醒）：补采会持续写库。
- [ ] 上线后跑一个真实的小任务（比如一个频道最近 24 小时），确认边界函数对真实的 Discord 返回（尤其是没有「读取消息历史」权限的频道返回空列表）的处理和 spec 一致。一页一次调用已经对照库源码核实过。
