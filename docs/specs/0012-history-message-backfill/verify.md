# Verify: 按时间段采集历史消息 · spec 0012 · updated 2026-11-05
_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

目前覆盖里程碑 1（打通细线）和里程碑 2（节奏和安全）。后面的里程碑做完时，在下面追加对应的步骤。

## UI / manual
- [x] 登录后打开 `/history`，点「新建任务」→ 只列小号能读、没删除、类型能采集的频道；没开实时采集的频道也能选 → AC-1
- [x] 新建任务对话框里，开了实时采集的频道在「正在采集」组；其他能读的频道在「其他频道（N）」组，默认收起；一个都没开实时采集时「其他频道」一开始就展开；搜索时两组一起搜 → AC-1
- [x] 窗口调矮后打开对话框 → 「取消」「建任务」一直固定在底部，中间内容自己滚动 → AC-1
- [x] 选一个开着采集的频道，点「最近 24 小时」建任务 → 已经被实时覆盖的时间不会建任务；同样再提交一次，结果显示「这段时间已经采齐了」 → AC-2
- [x] 选「全部历史」建任务 → 任务的起点是频道的创建时间 → AC-1
- [x] 结束时间填一个比现在晚的时间 → 任务的结束时间按现在算；开始时间晚于结束时间 → 不提交，提示「开始时间要早于结束时间」 → AC-1
- [x] collector 在线时 → 任务变成「抓取中」，进度和已抓条数大约 1 秒内更新；完成后从「进行中」消失 → AC-3, AC-14
- [x] 点任务行的频道名 → 打开 `/channels?id=<频道 ID>`，补回来的消息按时间出现在正确位置 → AC-14
- [x] 补回来的消息里有已经被实时采集存过的 → 库里仍然只有一行，`source` 仍是 `realtime` → AC-4
- [x] 收回小号对某个频道的读取权限后建任务 → 任务失败，显示「小号没有读取权限」，collector 日志里没有发出这一页的请求 → AC-3
- [ ] 对一个小号能看到、但没有「读取消息历史」权限的频道，选一段库里已有消息的时间建任务 → 任务失败，原因是 Discord 返回了空结果；没有写入新的覆盖段 → AC-3a

### 里程碑 2：节奏和安全
- [ ] 打开 `/history` → 顶部状态条显示「最近 24 小时已抓 N / 1500 页」，有「节奏设置」链接和「暂停全部」按钮 → AC-8, AC-19
- [ ] 点「暂停全部」→ 按钮变成「继续」，状态条显示「你手动暂停（时间）」；正在跑的任务当前页提交后回到「排队中」，之后 collector 不再发请求；点「继续」→ 原因清空，任务接着从断点跑 → AC-8, AC-10
- [ ] 建一个很大的任务（比如「全部历史」）观察 collector 日志 → 每页之间隔 2 到 4 秒；累计约 20 分钟后任务回到「排队中」，状态条和首页显示「休息中，<时间> 继续」，5 分钟后自动接着跑 → AC-5, AC-6
- [ ] 在 `/settings/history` 把「每 24 小时页数上限」改成 50 并保存，已用页数达到后 → 状态条显示「今日额度已用完，最早 <时间> 继续」，任务不领取、没有请求；改回 1500 后几秒内接着跑 → AC-5, AC-6, AC-19
- [ ] `/settings/history` 填超范围的值（如页数上限 10、最短间隔 9 而最长 4）→ 不保存，字段下显示范围提示 → AC-19
- [ ] 保存新的间隔后 → 不重启 collector，下一页开始用新间隔 → AC-19
- [ ] 手动让 `discord.http` 在 60 分钟内记 3 次限速警告（或在测试环境里模拟）→ 总开关变成「因限速自动暂停」，图标栏「历史采集」出现红点，`aria-label` 是「历史采集已因限速暂停」；点「继续」后红点消失，实时采集全程不受影响 → AC-7, AC-8
- [ ] 手动暂停期间再触发限速自动暂停的原因 → 原因保持最先的那个，不被覆盖 → AC-8
- [ ] 首页「系统状态」多了「历史采集」一行：正常显示最近心跳；暂停显示原因；休息和等限额显示继续的本地时间；停掉 collector 的历史任务循环（进程还活着）超过 180 秒显示红色「历史采集任务无响应」 → AC-20
- [ ] 抓取中杀掉 collector 再启动 → 任务回到「排队中」，断点保留，接着从断点跑，最多多抓一页、没有重复消息 → AC-10
- [ ] 小号掉线（断网）时 → 正在跑的任务回到「排队中」，恢复在线后继续 → AC-10

## Commands
- [x] `curl -i http://localhost:8080/api/history/jobs`（不带登录 cookie）→ 401 → AC-22
- [x] 登录后用错误的 `Origin` 头 POST `/api/history/jobs` → 403 `bad_origin`，没有建出任务 → AC-22
- [ ] `SELECT source, count(*) FROM message GROUP BY source` 在一个任务完成前后对比 → 只有 `history` 增加，数量和任务行的已抓条数一致 → AC-3
- [x] 任务完成后 `SELECT span, source FROM channel_coverage WHERE source = 'history'` → 有一段 `[range_start, range_end)` → AC-3
- [x] `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head` → 都成功；`SELECT * FROM history_settings` 有一行默认值（2、4、1500、20、5、6）
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest` → 全部通过
- [x] `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm check:contrast && pnpm build` → 全部通过
- [ ] `SELECT * FROM history_page_hour ORDER BY hour DESC LIMIT 3` 在抓取几页后 → 当前整点的 `pages` 随页数增加（含为了拿频道对象额外调用的那次）→ AC-5
- [ ] `SELECT history_waiting_reason, history_waiting_until, history_worker_seen_at FROM collector_heartbeat` → 休息或等限额时前两列有值，结束后为空；`history_worker_seen_at` 每 30 秒更新 → AC-6, AC-20
- [ ] 把 `discord.http` 的日志级别调成 ERROR 后启动 collector → 日志有 `history.ratelimit_watch_unhealthy`，`history_settings.paused_reason` 变成 `rate_limited` → AC-7
- [ ] `SELECT preempted_count FROM history_job` 在手动任务被 `gap_auto` 抢先后 → 加 1，抓到一页后清零；累计抢先 3 次后不再让位（缺口自动补在里程碑 4 才有，到时候再验）→ AC-10

## Value sourcing
- [x] 浏览器时区改成别的时区（比如 `America/New_York`）后选「最近 24 小时」→ 输入框显示的是新时区的本地时间，库里的 `range_start` 和 `range_end` 是对应的 UTC 时间
- [x] 「全部历史」的起点等于 `snowflake_time(频道 ID)`，不是 Discord 纪元，也不是任意固定时间
- [x] 任务的 `cursor_message_id` 是已抓到的最旧一条消息 ID，进度 = (结束时间 − 断点时间) / (结束时间 − 开始时间)
- [ ] 连续 5 次网络失败后，`not_before` 大约是数据库 `now()` 加 5 分钟（用数据库时钟，不是 collector 的时钟）
- [ ] 「最早可能继续的时间」= 窗口里按小时桶从早到晚累减、第一次低于上限的那一桶的 `hour` 加 25 小时（桶是 `history_page_hour` 的整点，用数据库时间）
- [ ] 429 次数只来自 `discord.http` 的 `We are being rate limited` 警告日志，实时会话和附件换链接的 429 也算
- [ ] 休息结束时间 = 开始休息的时间 + `history_settings.rest_minutes`；改设置后下一轮用新值

## Acceptance-criteria coverage
- AC-1：新建任务对话框的几步，加上时区那一步
- AC-2：「最近 24 小时」重复提交那一步
- AC-3：抓取进度、读取权限、入库数量、覆盖段这几步
- AC-3a：没有「读取消息历史」权限那一步
- AC-4：实时和补采去重那一步
- AC-14：进度更新、点频道名跳转那两步
- AC-22：401 和 Origin 那两条命令
- AC-5、AC-6：休息、额度、页数和心跳那几步
- AC-7、AC-8：限速自动暂停、总开关、红点那几步
- AC-10：杀掉 collector、掉线、抢先计数那几步
- AC-19：设置页保存和超范围那几步
- AC-20：首页「历史采集」一行
- AC-9、AC-11 到 AC-13、AC-15 到 AC-18、AC-21：属于后面的里程碑，到时候再补
