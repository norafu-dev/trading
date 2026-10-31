# Verify: 数据模型 · spec 0002 · updated 2026-10-31
_步骤来自 spec 0002 的验收标准。`/check verify` 照着跑，`/test` 把能长期保留的步骤写成正式测试。建议在一个临时库上跑（`createdb harvester_verify`，再把 `DATABASE_URL` 指向它）。_

## Commands

- [x] 在只有 pgvector 迁移的空库上 `uv run alembic upgrade head` → 建出 9 张表；`alembic downgrade -1` → 只剩 `alembic_version`；再 `upgrade head` 成功；`alembic check` 输出 `No new upgrade operations detected.` → AC-1
- [x] `uv run ruff check . && uv run ruff format --check . && uv run pyright` → 全部干净 → AC-12
- [x] `grep -n "commit()" backend/src/harvester/db/ingest.py` → 没有结果（写入函数不提交事务）→ AC-12
- [x] `grep -nE "delete\(|DELETE" backend/src/harvester/db/ingest.py` → 只出现在 `channel_coverage` 上 → Key invariants

## 写入行为（连临时库，调用 `harvester.db.ingest` 里的函数）

- [x] 依次写入服务器、分类、频道、作者、一条带两个附件、一个 embed（含值为 `None` 的键）和回复的消息 → 返回 `'inserted'`；读回字段一致，`embeds` 里没有 `None` 的键，`created_at` 等于 `snowflake_time(id)` → AC-2, AC-3
- [x] 同一条消息先 `realtime` 再 `history`（再反过来测一次）→ 一行，`source` 和 `ingested_at` 是第一次的值 → AC-2
- [x] 两个会话并发 `ingest_message` 同一条新消息 → 结果是 `'inserted'` 和 `'unchanged'`，没有异常，只有一行 → AC-2
- [x] 作者改了 `global_name` 再发一条 → `discord_user.global_name` 是新名字，旧消息的 `author_display_name` 不变 → AC-3
- [x] webhook（`kind='webhook'`）换名字再发一条 → `discord_user.username` 还是第一次的名字；机器人消息的作者 `kind='bot'` → AC-3, AC-4
- [x] 类型 7 和类型 20 的消息 → `'skipped_type'`，不入库 → AC-4
- [x] 编辑判断表逐行：已删除 → `'deleted'`；更旧的 `edited_at`（或快照为空、库里不为空）→ `'stale'` 且内容不变；更新的 `edited_at` 加新内容 → `'edited'`，`replaced_at` 是快照的 `edited_at`；更新的 `edited_at` 加相同内容 → `'unchanged'`，只改 `edited_at`；编辑时间相同、内容不同 → `'edited'`，`replaced_at` 是 `received_at`；编辑时间相同、只有 embed 不同 → `'embeds_refreshed'`，`version` 不变 → AC-5, AC-6
- [x] 连续编辑两次 → `version = 3`，`message_revision` 正好有版本 1、2 两行 → AC-5
- [x] 编辑时拿掉一个附件 → 它的 `removed_at` 是快照的 `edited_at`，`version` 不因此变化；留下的附件 `url` 被更新 → AC-5
- [x] `history` 来源的快照内容不同但编辑时间相同 → `'edited'`，旧内容进了 `message_revision` → AC-6
- [x] `mark_messages_deleted` 传入一个未删除、一个已删除、一个不存在的 ID → `marked`、`already_deleted`、`not_found` 分对；第二次标记不改 `deleted_at`；`content`、`embeds`、`forwarded`、附件、旧版本都不变；之后再收到编辑 → `'deleted'` → AC-7
- [x] `set_channel_status(..., 'deleted')` → 频道里消息的 `deleted_at` 仍为空；同值再调一次 → `'unchanged'`，`status_changed_at` 不变；不存在的 ID → `'not_found'` → AC-8
- [x] `DELETE FROM channel` 删一个有消息的频道 → 被外键拒绝 → AC-8
- [x] 给类型 4（分类）的频道设 `collect_enabled = true` → 被 `ck_channel_collectable_type` 拒绝；约束定义里的类型列表和 `COLLECTABLE_CHANNEL_TYPES` 一致 → AC-1
- [x] `open_realtime_coverage` 连续两次 → `'opened'`、`'already_open'`；关闭时间早于起点 → `'discarded_empty'` 且行被删；正常关闭 → `'closed'`，上界是关闭时间；没有开放段时关闭 → `'not_open'` → AC-10
- [x] 模拟崩溃：账号有 `last_heartbeat_at`，留两段开放的实时覆盖（一段起点早于心跳，一段晚于心跳，频道的 `collector_account_id` 为空）→ `close_stale_coverage` 返回 2；前一段的上界是心跳时间，后一段被删除 → AC-10
- [x] 转发消息写入后 `forwarded` 有值；再收到同 ID 的快照（`forwarded` 为空或不同）→ `forwarded` 不变 → AC-13
- [x] 函数返回的都是 `Literal` 或 `DeleteResult`；缺服务器、缺父频道、缺频道分别返回 `'missing_guild'`、`'missing_parent'`、`'missing_channel'`，没有异常 → AC-12

## 安全

- [x] 用 `harvester.logging` 记一条带 `token_ciphertext=...` 和 `token=...` 的日志 → 两个值都输出为 `***` → AC-9
- [x] `backend/openapi.json` 里没有任何响应模型带含 `token` 的字段 → AC-9

## 索引

- [x] 造 10 万条消息，**分布在多个频道、多个服务器、多个作者上**（只有一个频道时规划器会改走主键倒序扫描，测不到复合索引），`ANALYZE` 后对按频道、按作者、按服务器取最新 50 条分别 `EXPLAIN` → 都是 Index Scan，没有 Sort 节点 → AC-11

## Value sourcing

- [x] `created_at`：用 Discord 文档里的例子 `175928847299117063` → `2016-04-30 11:18:25.796 UTC`（整数运算，毫秒不丢）
- [x] `guild_id`：快照的 `guild_id` 和频道所属服务器不一致 → 被复合外键 `fk_message_channel_id_guild_id_channel` 拒绝
- [x] `ingested_at`、`first_seen_at`：等于数据库 `now()`（事务开始时间），不是 Discord 的时间
- [x] `version`：新消息为 1，即使快照入库前已有 `edited_at` 也不写旧版本
- [x] `replaced_at` 和 `attachment.removed_at`：快照有 `edited_at` 时用它；编辑时间相同时用 `received_at`
- [x] `deleted_at`：等于调用方传入的时间，不是数据库时间
- [x] `close_stale_coverage` 的上界：没有心跳（`last_heartbeat_at` 为空）时段被删除（上界等于起点，是空范围）
- [x] `source`：调用方传入的值原样保存，之后不再改变

## Acceptance-criteria coverage

- AC-1：迁移往返、`alembic check`、可采集类型约束 · AC-2：插入、重复来源、并发 · AC-3：插入、改名、webhook · AC-4：机器人和 webhook、跳过的类型 · AC-5：编辑判断表、连续编辑、附件 · AC-6：补采编辑 · AC-7：批量删除 · AC-8：频道状态、外键拒绝 · AC-9：日志脱敏、OpenAPI · AC-10：三个覆盖函数 · AC-11：索引 EXPLAIN · AC-12：检查命令、无 commit、返回值 · AC-13：转发
