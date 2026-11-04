# Verify: 按时间段采集历史消息 · spec 0012 · updated 2026-11-05
_Steps derived from spec 0012 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

目前只覆盖里程碑 1（打通细线）。后面的里程碑做完时，在下面追加对应的步骤。

## UI / manual
- [ ] 登录后打开 `/history`，点「新建任务」→ 只列小号能读、没删除、类型能采集的频道；没开实时采集的频道也能选 → AC-1
- [ ] 新建任务对话框里，开了实时采集的频道在「正在采集」组；其他能读的频道在「其他频道（N）」组，默认收起；一个都没开实时采集时「其他频道」一开始就展开；搜索时两组一起搜 → AC-1
- [ ] 窗口调矮后打开对话框 → 「取消」「建任务」一直固定在底部，中间内容自己滚动 → AC-1
- [ ] 选一个开着采集的频道，点「最近 24 小时」建任务 → 已经被实时覆盖的时间不会建任务；同样再提交一次，结果显示「这段时间已经采齐了」 → AC-2
- [ ] 选「全部历史」建任务 → 任务的起点是频道的创建时间 → AC-1
- [ ] 结束时间填一个比现在晚的时间 → 任务的结束时间按现在算；开始时间晚于结束时间 → 不提交，提示「开始时间要早于结束时间」 → AC-1
- [ ] collector 在线时 → 任务变成「抓取中」，进度和已抓条数大约 1 秒内更新；完成后从「进行中」消失 → AC-3, AC-14
- [ ] 点任务行的频道名 → 打开 `/channels?id=<频道 ID>`，补回来的消息按时间出现在正确位置 → AC-14
- [ ] 补回来的消息里有已经被实时采集存过的 → 库里仍然只有一行，`source` 仍是 `realtime` → AC-4
- [ ] 收回小号对某个频道的读取权限后建任务 → 任务失败，显示「小号没有读取权限」，collector 日志里没有发出这一页的请求 → AC-3
- [ ] 对一个小号能看到、但没有「读取消息历史」权限的频道，选一段库里已有消息的时间建任务 → 任务失败，原因是 Discord 返回了空结果；没有写入新的覆盖段 → AC-3a

## Commands
- [ ] `curl -i http://localhost:8080/api/history/jobs`（不带登录 cookie）→ 401 → AC-22
- [ ] 登录后用错误的 `Origin` 头 POST `/api/history/jobs` → 403 `bad_origin`，没有建出任务 → AC-22
- [ ] `SELECT source, count(*) FROM message GROUP BY source` 在一个任务完成前后对比 → 只有 `history` 增加，数量和任务行的已抓条数一致 → AC-3
- [ ] 任务完成后 `SELECT span, source FROM channel_coverage WHERE source = 'history'` → 有一段 `[range_start, range_end)` → AC-3
- [ ] `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head` → 都成功；`SELECT * FROM history_settings` 有一行默认值（2、4、1500、20、5、6）
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest` → 全部通过
- [ ] `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm check:contrast && pnpm build` → 全部通过

## Value sourcing
- [ ] 浏览器时区改成别的时区（比如 `America/New_York`）后选「最近 24 小时」→ 输入框显示的是新时区的本地时间，库里的 `range_start` 和 `range_end` 是对应的 UTC 时间
- [ ] 「全部历史」的起点等于 `snowflake_time(频道 ID)`，不是 Discord 纪元，也不是任意固定时间
- [ ] 任务的 `cursor_message_id` 是已抓到的最旧一条消息 ID，进度 = (结束时间 − 断点时间) / (结束时间 − 开始时间)
- [ ] 连续 5 次网络失败后，`not_before` 大约是数据库 `now()` 加 5 分钟（用数据库时钟，不是 collector 的时钟）

## Acceptance-criteria coverage
- AC-1：新建任务对话框的几步，加上时区那一步
- AC-2：「最近 24 小时」重复提交那一步
- AC-3：抓取进度、读取权限、入库数量、覆盖段这几步
- AC-3a：没有「读取消息历史」权限那一步
- AC-4：实时和补采去重那一步
- AC-14：进度更新、点频道名跳转那两步
- AC-22：401 和 Origin 那两条命令
- AC-5 到 AC-13、AC-15 到 AC-21：属于后面的里程碑，到时候再补
