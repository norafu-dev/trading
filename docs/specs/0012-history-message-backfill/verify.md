# Verify: 按时间段采集历史消息 · spec 0012 · updated 2026-11-05
_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

覆盖里程碑 1（打通细线）、里程碑 2（节奏和安全）、里程碑 3（操作和失败）、里程碑 4（缺口）和里程碑 5（测试和收口）。

## UI / manual
- [x] 登录后打开 `/history`，点「新建任务」→ 只列小号能读、没删除、类型能采集的频道；没开实时采集的频道也能选 → AC-1
- [x] 新建任务对话框里，开了实时采集的频道在「正在采集」组；其他能读的频道在「其他频道（N）」组，默认收起；一个都没开实时采集时「其他频道」一开始就展开；搜索时两组一起搜 → AC-1
- [x] 窗口调矮后打开对话框 → 「取消」「建任务」一直固定在底部，中间内容自己滚动 → AC-1
- [x] 选一个开着采集的频道，点「最近 24 小时」建任务 → 已经被实时覆盖的时间不会建任务；同样再提交一次，结果显示「这段时间已经采齐了」 → AC-2
- [x] 选「全部历史」建任务 → 任务的起点是频道的创建时间 → AC-1
- [x] 结束时间填一个比现在晚的时间 → 任务的结束时间按现在算；开始时间晚于结束时间 → 不提交，提示「开始时间要早于结束时间」 → AC-1
- [x] collector 在线时 → 任务变成「抓取中」，进度和已抓条数大约 1 秒内更新；完成后从「进行中」消失 → AC-3, AC-14a
- [x] 点任务行的频道名 → 打开 `/channels?id=<频道 ID>`，补回来的消息按时间出现在正确位置 → AC-14a
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

### 里程碑 3：操作和失败
- [ ] 排队中或抓取中的任务行 → 有「暂停」「取消」；点「暂停」→ 状态变「已暂停」，抓取中的在当前页提交后停下，之后 collector 日志里没有这个任务的新请求 → AC-9
- [ ] 已暂停的任务点「继续」→ 回到「排队中」，从断点接着抓；点「取消」→ 变「已取消」，从「进行中」移到「已结束」 → AC-9
- [ ] 失败的任务行 → 有「重试」「取消」；点「重试」→ 回到「排队中」，失败原因清空，进度从断点接着走 → AC-9
- [ ] 批次标题上的「整批暂停 / 整批继续 / 整批取消」→ 只在批次里有符合的行时出现，点击后只改符合的行，其他行不动 → AC-9
- [ ] 在两个标签页里打开 `/history`，一个页面点「暂停」后另一个页面再点同一行的「暂停」→ 提示「任务现在的状态不能做这个操作」，列表刷新成真实状态 → AC-9
- [ ] 取消一个已经抓了几页的任务，到频道页看 → 抓到的消息还在；再对同一段时间建任务 → 已抓到的部分被跳过，只剩没抓到的那段（断点之前） → AC-11
- [ ] 暂停一个已经抓了几页的任务，再建同一段时间的任务 → 不会跳过（暂停不写覆盖），但被未完成任务占着的时间段也不会重复建 → AC-2, AC-11
- [ ] 让小号读不到某个频道（收回权限）后重试一个失败任务 → 再次失败，原因「小号没有读取权限」；collector 日志没有发出这一页的请求 → AC-12
- [ ] 删掉 Discord 里的一个测试频道，对它建任务 → 任务失败，原因「频道已删除」，频道本身的状态不被任务改动；下一个任务等一个页间隔后继续 → AC-12
- [ ] 抓取中断网（或让 Discord 返回 5xx）→ 任务行显示「Discord 暂时不可用，稍后重试」和预计继续时间，间隔 30、60、120 秒翻倍；恢复后接着抓，等待提示消失 → AC-12
- [ ] 连续失败 5 次 → 任务回到「排队中」，约 5 分钟内不被领取，期间别的任务先跑 → AC-12
- [ ] 勾「整段重抓」对一个已经覆盖的时间段建任务，事先在 Discord 里删掉其中一条消息 → 任务完成后那条消息显示为已删除；没勾「整段重抓」时同样的删除不会被发现 → AC-13
- [ ] 选一个已经被实时采集覆盖了一部分的频道建一周的任务 → 同一个频道只显示一行和一个总进度条（按时间长度加权，几秒的小段不拉低它），行尾有「共 N 段，展开」；展开后列出每一段的时间、状态、进度；有子任务失败时默认展开，频道行显示「N 段失败：原因」→ AC-14
- [ ] 频道行的「暂停 / 继续 / 取消」→ 一次作用于这个频道下所有符合的子任务，批次里其他频道不动；失败的子任务在展开后点「重试」→ AC-9, AC-14
- [ ] 「已结束」区域 → 默认列最近 7 天结束的已完成和已取消任务；超过一页时有「加载更多」；切到「全部（保留 30 天）」后能看到更早的 → AC-14a

### 里程碑 4：缺口
- [ ] 让一个开着采集的频道留下缺口（关掉采集等几分钟再打开，或者重启 collector）→ `/history` 的「缺口」区域列出这个频道、段数、总时长和最早一段的时间 → AC-15
- [ ] 点这一行的「补缺口」→ 每一段缺口建一个 `origin = 'gap_manual'` 的任务（任务行的来源显示「一键补缺口」）；立刻再点一次 → 结果是「没有需要补的缺口」 → AC-15
- [ ] 多个频道都有缺口时点标题上的「全部补上」→ 每个频道各自按段建任务 → AC-15
- [ ] 一个频道只有一段开着的实时覆盖（从没断过）→ 「缺口」区域里没有它；全都没有缺口时显示「没有缺口，采集到的时间是连续的」 → AC-15
- [ ] 打开一个有缺口的频道页 → 消息列表上方显示「这个频道有 N 段缺口，共 X」和「补缺口」按钮；没有缺口的频道页只有「抓这个频道的历史」按钮 → AC-15, AC-18
- [ ] 在频道页点「抓这个频道的历史」→ 新建任务对话框打开，这个频道已经勾好；没开实时采集的频道页也有这个按钮，顶部仍然是「这个频道已关闭采集」的提示 → AC-18
- [ ] `auto_gap_max_hours` 保持默认 6，重启 collector（留下几秒的缺口）→ 覆盖对齐打开新段之后，自动建出来源是「自动补缺口」的任务并很快跑完 → AC-16
- [ ] 把一个频道的采集关掉 10 小时以上再打开 → 不自动建任务，缺口仍然列在「缺口」里等你一键补 → AC-16
- [ ] 在 `/settings/history` 把「自动补缺口的时长上限」改成 0，再重启 collector → 不建任何 `gap_auto` 任务，实时采集照常 → AC-16
- [ ] 打开一个频道页，往上滚到中间，同时让一个补采任务往这个频道写消息 → 5 秒内补回来的消息按时间插到正确位置，你正在看的那条不跳动，也没有未读圆点以外的闪动 → AC-17
- [ ] 补采写入期间看浏览器的网络面板 → 没有 `new_message` 事件，只有 `history_changed`；未读数按已读位置算（比已读位置旧的补采消息不算未读）→ AC-17

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
- [ ] `SELECT status, error, finished_at FROM history_job WHERE id = <被取消的失败任务>` → `status = 'cancelled'`，`error` 为空 → AC-9
- [ ] 取消前后对比 `SELECT span, source FROM channel_coverage WHERE channel_id = <频道> AND source = 'history'` → 取消一个有断点的 `running` 或 `paused` 任务后多一段 `[断点时间 + 1 毫秒, range_end)`；没有断点的、从 `failed` 取消的、只暂停的都不多 → AC-11
- [ ] `SELECT delete_source, count(*) FROM message WHERE deleted_at IS NOT NULL GROUP BY delete_source` 在整段重抓任务前后对比 → 只有 `reconciled` 增加，数量等于 Discord 上确实删掉的条数 → AC-13
- [ ] 在 collector 容器里等一个小时（或临时把 `CLEANUP_INTERVAL` 调小）→ 日志有 `history.cleanup`；`finished_at` 早于 30 天前的任务行被删，覆盖段和消息不动，48 小时前的 `history_page_hour` 行被删 → AC-21
- [ ] `curl -i -X POST http://localhost:8080/api/history/jobs/1/pause`（不带登录 cookie）和 `.../batches/<uuid>/pause` → 401；登录后用错误的 `Origin` → 403，任务状态不变 → AC-22
- [ ] `curl -i http://localhost:8080/api/history/gaps` 和 `curl -i -X POST .../api/history/gaps/fill -d '{"channel_ids":["<频道>"]}'`（不带登录 cookie）→ 都是 401；登录后用错误的 `Origin` POST → 403，没有建出任务 → AC-22
- [ ] `SELECT origin, count(*) FROM history_job GROUP BY origin` 在一键补和自动补之后 → 分别多出 `gap_manual` 和 `gap_auto` 的行 → AC-15, AC-16
- [ ] 让 `create_gap_jobs` 抛异常（临时改代码或改掉设置表）后重启 collector → 日志有 `history.auto_gap_failed` 警告，实时覆盖段照常打开，采集不受影响 → AC-16
- [ ] `SELECT preempted_count FROM history_job` 在手动任务被 `gap_auto` 抢先后 → 加 1，抓到一页后清零；连续被抢先 3 次后不再让位 → AC-10
- [ ] `cd backend && uv run pytest tests/db/test_history_gaps.py tests/api/test_history_routes.py` → 全部通过（缺口段、自动补上限、一键补扣减未完成任务、两个接口的鉴权）→ AC-15, AC-16, AC-22

### 里程碑 5：测试和收口
- [x] `cd backend && uv run pytest` → 879 项全部通过（spec 的 Critical test scenarios 都有对应测试）→ AC-1 到 AC-22
- [x] `cd web && pnpm test` → 485 项全部通过 → AC-1, AC-7, AC-9, AC-14, AC-14a, AC-15, AC-17, AC-18, AC-19, AC-20
- [x] `cd backend && uv run python -m harvester.api.export_openapi` 之后 `git diff --exit-code openapi.json` → 没有改动，导出的接口和仓库里的一致
- [x] `cd web && pnpm gen:api` 之后 `git diff --exit-code src/lib/api/schema.d.ts` → 没有改动，前端类型和 `backend/openapi.json` 同步
- [x] `cd backend && uv run pytest tests/collector/test_ratelimit_watch.py::test_installed_library_still_uses_the_wording_we_match` → 通过；升级 `discord.py-self` 后库改了限速警告的措辞，这一项会失败 → AC-7
- [x] `cd backend && uv run pytest tests/db/test_history.py -k notifies` → 通过：每页提交只发 `history_changed`，不发 `new_message` → AC-3, AC-17
- [x] `cd backend && uv run pytest tests/collector/test_supervisor.py -k auto_gap` → 通过：对齐后自动建 `gap_auto`，建任务出错时实时覆盖段照常打开 → AC-16
- [x] `cd backend && uv run pytest tests/collector/test_history_worker.py -k settings_change` → 通过：改了节奏设置下一页就用新值，不用重启 → AC-19
- [x] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright` → 全部通过
- [x] `cd web && pnpm lint && pnpm typecheck && pnpm check:contrast && pnpm build` → 全部通过

## Value sourcing
- [x] 浏览器时区改成别的时区（比如 `America/New_York`）后选「最近 24 小时」→ 输入框显示的是新时区的本地时间，库里的 `range_start` 和 `range_end` 是对应的 UTC 时间
- [x] 「全部历史」的起点等于 `snowflake_time(频道 ID)`，不是 Discord 纪元，也不是任意固定时间
- [x] 任务的 `cursor_message_id` 是已抓到的最旧一条消息 ID，进度 = (结束时间 − 断点时间) / (结束时间 − 开始时间)
- [ ] 连续 5 次网络失败后，`not_before` 大约是数据库 `now()` 加 5 分钟（用数据库时钟，不是 collector 的时钟）
- [ ] 「最早可能继续的时间」= 窗口里按小时桶从早到晚累减、第一次低于上限的那一桶的 `hour` 加 25 小时（桶是 `history_page_hour` 的整点，用数据库时间）
- [ ] 429 次数只来自 `discord.http` 的 `We are being rate limited` 警告日志，实时会话和附件换链接的 429 也算
- [ ] 休息结束时间 = 开始休息的时间 + `history_settings.rest_minutes`；改设置后下一轮用新值
- [ ] 取消或失败时覆盖段的起点 = 断点消息的时间加 1 毫秒（同一毫秒里更小的 ID 还没抓到），终点 = `range_end`，不是 `range_start`，也不是当前时间
- [ ] 整段重抓对账的 ID 区间：上界 = 这一页请求的 `before_id`（第一页是 `range_end` 对应的 ID），下界 = 这一页最旧一条的 ID，最后一页和空页是 `range_start` 对应的 ID；`deleted_at` 是这一页提交时的数据库时间，`delete_source = 'reconciled'`
- [ ] 缺口段来自 `range_agg`：覆盖 `[1点, 3点)` 和 `[5点, ∞)` 的频道，缺口正好是 `[3点, 5点)`；开放的实时段算到数据库 `now()`，不是浏览器时间
- [ ] 自动补的时长上限读的是 `history_settings.auto_gap_max_hours`（改成 12 之后，原来不补的 10 小时缺口会自动补）
- [ ] 一键补的每一行时间段等于缺口本身减去未完成任务占着的时间，不是整段时间

## Acceptance-criteria coverage
- AC-1：新建任务对话框的几步，加上时区那一步
- AC-2：「最近 24 小时」重复提交那一步
- AC-3：抓取进度、读取权限、入库数量、覆盖段这几步
- AC-3a：没有「读取消息历史」权限那一步
- AC-4：实时和补采去重那一步
- AC-14：按频道合并、折叠子任务、频道级操作那两步
- AC-14a：进度更新、点频道名跳转、「已结束」分页那几步
- AC-22：401 和 Origin 那两条命令
- AC-5、AC-6：休息、额度、页数和心跳那几步
- AC-7、AC-8：限速自动暂停、总开关、红点那几步
- AC-10：杀掉 collector、掉线、抢先计数那几步
- AC-19：设置页保存和超范围那几步
- AC-20：首页「历史采集」一行
- AC-9、AC-11、AC-12、AC-13、AC-21：里程碑 3 那几步（任务操作、部分覆盖段、失败处理、整段重抓对账、清理）；AC-14 和 AC-14a 的「已结束」分页、按频道合并那几步也在里面
- AC-15：「缺口」区域、「补缺口」「全部补上」、频道页提示那几步
- AC-16：自动补短缺口、超过上限不补、上限设为 0 那几步
- AC-17：频道页 5 秒合并重取、不发 `new_message` 那两步
- AC-18：频道页「抓这个频道的历史」那一步
