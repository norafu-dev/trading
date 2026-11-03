# 0002. 核心数据模型

**Date**: 2026-10-31
**Status**: Accepted

## Summary

这个 spec 定下采集器所有功能共用的表：你的小号、服务器、频道、发言人、消息、消息旧版本、附件、已采齐的时间段、历史任务、关键词，以及以后给 AI 用的变更记录。服务器、频道、用户、消息直接用 Discord 的 ID 做主键，所以同一条消息不管是实时采到还是补采到，都只存一份。消息表永远存最新内容，编辑前的旧内容进版本表，删除只打标记不删原文，以后的下单程序可以凭 `version` 判断自己依据的内容是否已经过时。第 3 项先建 Slice 1 要用的表，其余表在对应切片里用只新增的迁移补上。

## Requirements

**User stories**:
- 作为你，我想让实时和补采的消息合在一起不重复，这样同一条消息只看到一次。
- 作为你，我想在 KOL 改错或删掉消息后看到最新内容，同时还能查到原文。
- 作为以后的 AI 和下单程序，我想知道一条消息现在是第几版、是否已删除、是实时还是补采来的，这样不会依据过时或补回来的旧消息下单。
- 作为你，我想让账号 token 只以密文存在库里。

**Acceptance criteria**:
- **AC-1**: 在只有 pgvector 迁移的库上执行 `alembic upgrade head`，会建出第 3 项的全部表、约束和索引；`alembic downgrade -1` 按外键的反向顺序删表，干净回退到 pgvector 版本；再次 `upgrade head` 成功。
- **AC-2**: 同一个消息 ID 先以 `realtime` 写入、再以 `history` 写入（反过来也一样），库里只有一行，`source` 和 `ingested_at` 保持第一次写入的值；两个并发的 `ingest_message` 也不会报错或产生两行。
- **AC-3**: 写入消息时，作者会自动新建或更新到 `discord_user`。作者改名后，`discord_user` 显示新名字，旧消息的 `author_display_name` 保持发言时的名字。
- **AC-4**: 机器人和 webhook 发的普通消息和回复照常存储，作者的 `kind` 分别是 `bot` 和 `webhook`；类型不是 0 或 19 的消息（系统消息、斜杠命令回复等）返回 `'skipped_type'`，不入库。
- **AC-5**: 已存消息再次收到时，严格按「编辑判断表」处理：真正的编辑把旧内容作为一行写进 `message_revision`，`version` 加 1；只是 embed 展开时直接更新 `embeds`，不加版本；比库里更旧的数据（`stale`）不会让内容倒退。
- **AC-6**: 历史补采发现已存消息的内容不同，按 AC-5 的同一张判断表处理（内容不同但编辑时间没变时，也算编辑，旧内容不丢）。
- **AC-7**: 标记删除（单条或批量）时，`deleted_at` 和 `delete_source`（`realtime` 或 `reconciled`）被填上，`content`、`embeds`、`forwarded`、附件和旧版本都不变；重复标记不会覆盖第一次的删除时间；已删除的消息再收到编辑，返回 `'deleted'` 并忽略。
- **AC-8**: 服务器和频道从不硬删除：离开服务器、频道被删、线程归档都只改 `status` 和 `status_changed_at`，不会把频道里的消息标成已删除；有消息的频道在数据库层面删不掉（外键 `RESTRICT`）。
- **AC-9**: token 只以密文存在 `discord_account.token_ciphertext`；api 的任何**响应**模型里都没有 `token_ciphertext` 或明文 token 字段；日志里这两个键被替换成 `***`。
- **AC-10**: 实时覆盖记录遵守「覆盖规则」：每个频道最多同时有一段开放的实时覆盖；collector 启动时关掉上次没关的段，关闭时间不会早于这一段的起点，长度为 0 的段被删除。
- **AC-11**: 插入 10 万条测试消息并 `ANALYZE` 后，按频道、按作者、按服务器各取最新 50 条，`EXPLAIN` 都是索引扫描，并且没有 Sort 节点。
- **AC-12**: 新增的代码通过 `pyright` strict 和 `ruff`；所有写入函数都不自己提交事务，并用 `Literal` 联合类型或 frozen dataclass 表达「重复」「找不到」「数据太旧」等可预期结果，不抛异常。
- **AC-13**: 转发消息的被转发内容（文字、embed、原消息和原频道的 ID）存进 `message.forwarded`，第一次写入后不再改变。

## Decision

**Chosen option**: Option 1: 以 Discord ID 为主键的关系模型，消息表存最新版，版本表存旧版

全部表都用 SQLAlchemy 2.x 模型定义在 `harvester.db`，用 Alembic 迁移建表；第 3 项只建 Slice 1 需要的表（另外加上 `message_revision`，原因见 Build plan），其余表由各自的功能用只新增的迁移补上。

**Implementation skills**: `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### 通用约定

- 主键：Discord 实体（服务器、频道、用户、消息、附件）用 Discord ID，类型 `BIGINT`；自建实体用 `BIGINT GENERATED ALWAYS AS IDENTITY`；历史任务的批次号用 `UUID`（`uuidv7()`，第 10 项）。
- 状态和类别字段用 `TEXT` 加 `CHECK (col IN (...))`，不用 PostgreSQL 的 `ENUM` 类型，以后加值只需要改约束。
- 时间一律 `TIMESTAMPTZ`（UTC）。所有默认值是 `now()` 的列（`created_at`、`updated_at`、`first_seen_at`、`status_changed_at`、`checked_at`、`ingested_at`）都用数据库默认值（`server_default`），不靠 Python。
- `updated_at` 不用 ORM 的 `onupdate`（它对 `ON CONFLICT DO UPDATE` 不生效），每个更新语句都在 `SET` 里显式写 `updated_at = now()`。
- 字符串一律 `TEXT`。空的 embed 列表一律存 `NULL`，不存 `[]`。
- 外键默认 `ON DELETE RESTRICT`，例外在表里单独写明。每个外键列都有索引（主键或复合索引的前缀已经覆盖的除外）。
- 每个 `CHECK`、每个复合索引、部分索引都在模型的 `__table_args__` 里声明并显式写 `name=`（命名约定 `ck_%(table_name)s_%(constraint_name)s` 要求必须有名字），这样模型和迁移不会对不上。约束名沿用 `harvester.db.base.NAMING_CONVENTION`。
- 时间范围一律是 `[)` 边界（包含起点，不包含终点）。

### Data model sketch

**`discord_account`**（你的小号）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint identity | 是 | 主键 |
| `discord_user_id` | bigint | 否 | 第一次登录成功后由 collector 填入；唯一 |
| `label` | text | 是 | 你起的备注名 |
| `token_ciphertext` | bytea | 是 | 加密后的 token；算法由第 6 项定 |
| `token_key_version` | smallint | 是 | 加密所用主密钥的版本号，方便以后换密钥 |
| `status` | text | 是 | `connecting` / `online` / `reconnecting` / `token_invalid` / `banned` / `disabled`，默认 `connecting` |
| `status_changed_at` | timestamptz | 是 | 默认 `now()` |
| `last_error` | text | 否 | 最近一次错误的说明（不含 token） |
| `last_heartbeat_at` | timestamptz | 否 | 只在 `status = 'online'` 时更新，频率不低于每 60 秒一次（具体由第 9 项定） |
| `created_at`、`updated_at` | timestamptz | 是 | |

**`guild`**（服务器）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint | 是 | Discord ID，主键 |
| `name` | text | 是 | 最新名字 |
| `icon_hash` | text | 否 | |
| `status` | text | 是 | `active` / `left`（所有账号都已不在）/ `deleted`，默认 `active` |
| `status_changed_at` | timestamptz | 是 | |
| `first_seen_at`、`updated_at` | timestamptz | 是 | |

**`channel`**（频道、分类、线程、论坛帖子，在 Discord 里都是频道）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint | 是 | Discord ID，主键 |
| `guild_id` | bigint | 是 | 外键 → `guild` |
| `parent_id` | bigint | 否 | 外键 → `channel`。普通频道指向所属分类（没有分类就是空），线程和论坛帖子指向父频道 |
| `type` | smallint | 是 | Discord 的频道类型编号 |
| `name` | text | 是 | |
| `position` | integer | 否 | 列表排序用，来自 Discord；线程没有 |
| `collect_enabled` | boolean | 是 | 默认 `false`。每个频道、每个线程各自一个开关，线程不跟随父频道 |
| `collector_account_id` | bigint | 否 | 外键 → `discord_account`，`ON DELETE SET NULL`。负责采集它的账号，由第 7 项在打开开关时设置 |
| `status` | text | 是 | `active` / `archived`（仅线程）/ `deleted`，默认 `active` |
| `status_changed_at` | timestamptz | 是 | |
| `first_seen_at`、`updated_at` | timestamptz | 是 | |

约束和索引：
- 唯一约束 (`id`, `guild_id`)，给 `message` 的复合外键用。
- 可采集类型：`CHECK (NOT collect_enabled OR type IN (0, 5, 10, 11, 12))`，也就是文字频道 (0)、公告频道 (5)、公告线程 (10)、公开线程 (11)、私密线程 (12)。论坛 (15) 和媒体 (16) 频道本身没有消息，它们的帖子是类型 11 的线程。分类 (4) 和语音频道 (2、13) 也会存（dashboard 分组要用），但不能打开开关。同一份列表在 `harvester.db` 里有一个常量 `COLLECTABLE_CHANNEL_TYPES`，测试要确认两边一致。
- 索引：`guild_id`、`parent_id`。

**`account_channel_access`**（哪个账号能看到哪个频道，由第 7 项写入）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account_id` | bigint | 是 | 外键 → `discord_account`，`ON DELETE CASCADE` |
| `channel_id` | bigint | 是 | 外键 → `channel` |
| `can_read` | boolean | 是 | 这个账号现在能否读取历史消息 |
| `checked_at` | timestamptz | 是 | 最后一次确认的时间 |

主键 (`account_id`, `channel_id`)，另加索引 `channel_id`。dashboard 上的「无权限」由这张表算出来：没有任何账号 `can_read = true`。

**`discord_user`**（发言人）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint | 是 | Discord ID，主键；webhook 消息用 webhook ID |
| `kind` | text | 是 | `user` / `bot` / `webhook`；只在第一次插入时设置 |
| `username` | text | 是 | 最新的用户名（webhook 是第一次见到时的名字） |
| `global_name` | text | 否 | 最新的显示名 |
| `avatar_hash` | text | 否 | |
| `first_seen_at`、`updated_at` | timestamptz | 是 | |

只存发过消息的人；被回复、被提及但没发言的人不存。

**`message`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint | 是 | Discord ID，主键 |
| `channel_id` | bigint | 是 | 复合外键 (`channel_id`, `guild_id`) → `channel` (`id`, `guild_id`) |
| `guild_id` | bigint | 是 | 同上，保证和频道所属服务器一致 |
| `author_id` | bigint | 是 | 外键 → `discord_user` |
| `author_display_name` | text | 是 | 发言那一刻显示的名字 |
| `type` | smallint | 是 | Discord 消息类型，只允许普通消息 (0) 和回复 (19) |
| `content` | text | 是 | 最新的文字，可以是空字符串 |
| `embeds` | jsonb | 否 | 最新的 embed 列表（`EmbedData`）；没有就是 `NULL` |
| `forwarded` | jsonb | 否 | 被转发的内容（`ForwardedData`）；只有转发消息才有，写入后不变 |
| `reply_to_id` | bigint | 否 | 被回复的消息 ID；不加外键 |
| `version` | integer | 是 | 默认 1，每次编辑加 1 |
| `created_at` | timestamptz | 是 | 由 ID 算出的发送时间 |
| `edited_at` | timestamptz | 否 | Discord 给出的最后编辑时间 |
| `deleted_at` | timestamptz | 否 | |
| `delete_source` | text | 否 | `realtime` / `reconciled`；和 `deleted_at` 同时为空或同时有值 |
| `source` | text | 是 | `realtime` / `history`，第一次写入的方式 |
| `ingested_at` | timestamptz | 是 | 默认 `now()`，第一次入库时间 |

索引：(`channel_id`, `id` DESC)、(`author_id`, `id` DESC)、(`guild_id`, `id` DESC)。约束：`CHECK (type IN (0, 19))`、`CHECK (version >= 1)`、`CHECK ((deleted_at IS NULL) = (delete_source IS NULL))`、`source` 和 `delete_source` 的取值 `CHECK`。

两个 `TypedDict`，都在 `harvester.db`，只保留拆好的字段（由 collector 从 Discord 对象转换，有意丢掉视频、提供方、代理链接等字段）：
- `EmbedData`：`type`、`title`、`description`、`url`、`color`、`timestamp`、`author`（`name`、`url`）、`fields`（`name`、`value`、`inline` 的列表）、`footer_text`、`image_url`、`thumbnail_url`。值为 `None` 的键不写入。
- `ForwardedData`：`message_id`、`channel_id`、`guild_id`（可空）、`content`、`embeds`（`EmbedData` 列表）、`attachment_names`（被转发消息的附件文件名列表；附件本身不下载）。

**`message_revision`**（被替换掉的旧版本）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message_id` | bigint | 是 | 外键 → `message` |
| `version` | integer | 是 | 这是第几版（写入时等于编辑前的 `message.version`） |
| `content` | text | 是 | 当时的文字 |
| `embeds` | jsonb | 否 | 当时的 embed |
| `replaced_at` | timestamptz | 是 | 被新版本替换的时间 |

主键 (`message_id`, `version`)。一条 `version = N` 的消息，正好有版本 1 到 N−1 的记录。第 k 版的生效时间是第 k−1 版的 `replaced_at`（第 1 版是 `created_at`）。

**`attachment`**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint | 是 | Discord 附件 ID，主键 |
| `message_id` | bigint | 是 | 外键 → `message`；有索引 |
| `filename` | text | 是 | Discord 给的原名，不做处理（第 12 项保存文件时再处理） |
| `content_type` | text | 否 | |
| `size_bytes` | bigint | 是 | |
| `url` | text | 是 | 最近一次收到的 Discord 链接（会过期，每次收到都更新） |
| `width`、`height` | integer | 否 | 图片和视频才有 |
| `removed_at` | timestamptz | 否 | 编辑时附件被拿掉的时间 |

**`channel_coverage`**（已采齐的时间段）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | bigint identity | 是 | 主键 |
| `channel_id` | bigint | 是 | 外键 → `channel`；有索引 |
| `span` | tstzrange | 是 | `[)` 时间范围；上界开放表示实时采集还在进行；`CHECK (NOT isempty(span))` |
| `source` | text | 是 | `realtime` / `history` |
| `created_at` | timestamptz | 是 | |

部分唯一索引：`(channel_id) WHERE source = 'realtime' AND upper_inf(span)`，保证每个频道最多一段开放的实时覆盖。不同的段可以重叠；计算缺口时用 `range_agg(span)` 合并（由第 10 项实现）。

**以后的表（第 3 项不建，结构先定下来）**

- `history_job`（第 10 项建）：`id` identity、`batch_id` uuid、`channel_id` → `channel`、`account_id` → `discord_account`（`SET NULL`）、`range_start`、`range_end`（timestamptz，`CHECK (range_start < range_end)`）、`status`、`cursor_message_id`（断点，可空）、`fetched_count`（默认 0）、`error`、`created_at`、`started_at`、`finished_at`、`updated_at`。索引：`(id) WHERE status = 'pending'`、`channel_id`、`batch_id`。
- `attachment` 补字段（第 12 项）：存储位置、下载状态、下载时间。
- 搜索索引（第 13 项决定）。
- `keyword`（第 15 项建）：`id` identity、`pattern` text（`CHECK (length(pattern) BETWEEN 1 AND 200)`）、`match_type` text 默认 `contains`（`CHECK (match_type IN ('contains'))`，以后加 `regex`）、`enabled` boolean、`created_at`；唯一索引 (`lower(pattern)`, `match_type`)。匹配时不区分大小写，匹配范围包括 `content` 和 `forwarded` 的文字。
- `keyword_hit`（第 15 项建）：`keyword_id` → `keyword`（`CASCADE`）、`message_id` → `message`、`matched_at`、`read_at`（可空）；主键 (`keyword_id`, `message_id`)，部分索引 `WHERE read_at IS NULL`。
- `message_change`（AI 功能建）：`seq` identity 主键、`message_id` → `message`、`kind`（`created` / `edited` / `deleted`）、`version`、`occurred_at`。和消息的写入在同一个事务里追加，只追加不修改。下游程序记住自己处理到的 `seq`。
- 向量列（AI 功能决定维度）。

### 实体关系

```
discord_account 1 ─ N account_channel_access N ─ 1 channel
discord_account 1 ─ N channel (collector_account_id)
guild           1 ─ N channel
channel         1 ─ N channel (parent_id：分类、线程)
channel         1 ─ N message   （复合外键 channel_id + guild_id）
discord_user    1 ─ N message
message         1 ─ N message_revision
message         1 ─ N attachment
channel         1 ─ N channel_coverage
channel         1 ─ N history_job          （第 10 项）
keyword         N ─ N message 经 keyword_hit（第 15 项）
message         1 ─ N message_change       （AI 功能）
```

### State transitions

- **`discord_account.status`**：`connecting` → `online`；`online` ⇄ `reconnecting`（网络问题，collector 自己重连）；任意状态 → `token_invalid` 或 `banned`（collector 写入后空转，不再登录）；你在 dashboard 更新 token 后 → `connecting`；你停用 → `disabled`，再启用 → `connecting`。具体触发细节由第 6 项定。
- **`channel.status`**：`active` ⇄ `archived`（仅线程，由 Discord 事件触发）；`active` 或 `archived` → `deleted`（终态）。
- **`guild.status`**：`active` ⇄ `left`（最后一个账号离开或重新加入）；→ `deleted`（终态）。
- **`message`**：版本从 1 开始只增不减；`deleted_at` 一旦填上就不再改变。
- **`history_job.status`**（第 10 项可以细化）：`pending` → `running`（collector 用 `FOR UPDATE SKIP LOCKED` 领取）→ `done` / `failed`；`running` → `paused` 或 `cancelled`（api 改状态，collector 每取完一页检查一次）；`paused` → `pending`（继续）；`failed` → `pending`（重试）；collector 启动时把自己账号下的 `running` 改回 `pending`。

### API surface

这个 spec 不新增 HTTP 接口，接口由用到这些表的功能自己定。它交付的是 `harvester.db` 里的数据库函数，第 3 项只有 collector 调用；api 在后续功能里只读这些表，加上第 7 项改开关、第 6 项写 token。

所有函数都接收显式传入的 `AsyncSession`，**都不提交事务**：调用方一个 Discord 事件一个事务，`NOTIFY` 也在同一个事务里（spec 0001）。可预期的结果用返回值表达；只有真正意外的数据库错误才抛异常。

| 函数 | 关键输入 | 返回 |
|---|---|---|
| `upsert_guild(session, snap)` | `GuildSnapshot` | `None` |
| `upsert_channel(session, snap)` | `ChannelSnapshot` | `'ok' \| 'missing_guild' \| 'missing_parent'` |
| `set_guild_status(session, guild_id, status, at)` | ID、状态、时间 | `'changed' \| 'unchanged' \| 'not_found'` |
| `set_channel_status(session, channel_id, status, at)` | ID、状态、时间 | `'changed' \| 'unchanged' \| 'not_found'` |
| `upsert_user(session, snap)` | `UserSnapshot` | `None` |
| `ingest_message(session, snap, source, received_at)` | `MessageSnapshot`、`'realtime' \| 'history'`、时间 | `'inserted' \| 'edited' \| 'embeds_refreshed' \| 'unchanged' \| 'stale' \| 'deleted' \| 'skipped_type' \| 'missing_channel'` |
| `mark_messages_deleted(session, ids, at, source)` | ID 元组、时间、`'realtime' \| 'reconciled'` | `DeleteResult`（frozen dataclass：`marked`、`already_deleted`、`not_found` 三个 ID 元组） |
| `open_realtime_coverage(session, channel_id, at)` | ID、时间 | `'opened' \| 'already_open'` |
| `close_realtime_coverage(session, channel_id, at)` | ID、时间 | `'closed' \| 'discarded_empty' \| 'not_open'` |
| `close_stale_coverage(session, account_id)` | 账号 ID | 关掉的段数 `int` |

`GuildSnapshot`、`ChannelSnapshot`、`UserSnapshot`、`MessageSnapshot`、`AttachmentSnapshot` 都是 `frozen` dataclass，定义在 `harvester.db.snapshots`。把 `discord.py-self` 的对象转换成它们，是 collector 边界的事（第 6、7、8 项），`harvester.db` 不导入 Discord 库。

### 写入规则

**写入顺序**（同一个事务里）：服务器 → 父频道（分类或线程的父频道）→ 频道 → 作者 → 消息 → 附件。
- `upsert_channel` 发现服务器或父频道不在库里，就返回 `'missing_guild'` 或 `'missing_parent'`，不写入；调用方先补上级再重试。父频道没开采集的线程照样存（只是开关默认关）。
- `ingest_message` 发现频道不在库里，返回 `'missing_channel'`；调用方先 `upsert_channel` 再重试。判断用一次 `SELECT`，不靠捕获外键错误。

**各表冲突时更新哪些列**（都带 `WHERE ... IS DISTINCT FROM`，没变就不重写，避免每条消息都改一遍）：

| 表 | 冲突时更新 | 从不由 upsert 更新 |
|---|---|---|
| `guild` | `name`、`icon_hash`、`updated_at` | `status`、`status_changed_at`、`first_seen_at` |
| `channel` | `name`、`type`、`parent_id`、`position`、`updated_at` | `guild_id`、`collect_enabled`、`collector_account_id`、`status`、`status_changed_at`、`first_seen_at` |
| `discord_user`（`kind` 是 `user` 或 `bot`） | `username`、`global_name`、`avatar_hash`、`updated_at` | `kind`、`first_seen_at` |
| `discord_user`（`kind = 'webhook'`） | 不更新（webhook 每条消息可以换名字，名字已在 `author_display_name` 里） | 全部 |
| `attachment` | `url`、`content_type`、`width`、`height` | 其他列 |

`status` 只通过 `set_*_status` 改，并且只在值真正变化时更新 `status_changed_at`。

**`ingest_message` 流程**：
1. 类型不是 0 或 19 → 返回 `'skipped_type'`。
2. 频道不存在 → `'missing_channel'`。
3. `upsert_user`（每次都做，保持名字最新）。
4. `SELECT ... FOR UPDATE` 取已有的行。没有就 `INSERT ... ON CONFLICT (id) DO NOTHING RETURNING id`：插入成功就写附件，返回 `'inserted'`；被并发写入抢先（没有返回行）就重新 `SELECT ... FOR UPDATE`，进入第 5 步。
5. 已有的行按下面的编辑判断表处理；除了 `stale` 和 `deleted`，都同步附件。

新消息第一次入库时，`version` 固定为 1、`edited_at` 取快照里的值（入库前就被编辑过的消息也是这样），不写旧版本。

**编辑判断表**（S 是收到的快照，D 是库里的行；embed 比较的是统一格式后的 Python 结构：去掉值为 `None` 的键，空列表当成 `None`）：

| 条件（从上往下，第一条命中的生效） | 结果 | 写入 |
|---|---|---|
| D 已删除 | `'deleted'` | 什么都不写 |
| S.edited_at 为空，D.edited_at 不为空；或两者都不为空且 S.edited_at < D.edited_at | `'stale'` | 什么都不写 |
| S.edited_at 比 D.edited_at 新（包括 D 为空），并且内容或 embed 不同 | `'edited'` | 旧版本行（`version` = D.version，`replaced_at` = S.edited_at），`version + 1`，更新 `content`、`embeds`、`edited_at` |
| S.edited_at 比 D.edited_at 新，内容和 embed 都相同 | `'unchanged'` | 只更新 `edited_at`（比如只拿掉了附件） |
| 编辑时间相同（包括都为空），内容不同 | `'edited'` | 同上，但 `replaced_at` = `received_at`（数据不一致时以不丢内容为准） |
| 编辑时间相同，只有 embed 不同 | `'embeds_refreshed'` | 只更新 `embeds`，不加版本 |
| 其余情况 | `'unchanged'` | 什么都不写 |

`forwarded`、`reply_to_id`、`author_display_name`、`source`、`ingested_at` 只在插入时写，之后不改。

**附件同步**：快照里有、库里没有的就插入；库里有、快照里没有并且 `removed_at` 为空的，就把 `removed_at` 设成 S.edited_at（为空就用 `received_at`）；两边都有的，更新 `url` 等可变列。附件变化不加版本号，也不写旧版本。

**删除规则**：
- 实时删除事件（单条和批量都一样）用 `mark_messages_deleted(..., source='realtime')`，时间是收到事件的时间。只更新 `deleted_at IS NULL` 的行。库里没有的 ID 进 `not_found`，直接忽略。
- 频道被删、线程被删只调用 `set_channel_status`，不动里面的消息。
- 补采对账（第 10 项实现，规则在这里定）：**只有任务状态是 `done` 时才对账**；对比范围是这个任务 `[range_start, range_end)` 换算成的消息 ID 范围；库里有、这次补采没返回、还没删除的消息，用 `source='reconciled'`、发现时间来标记。暂停、取消、失败、被限速中断的任务一律不对账。

**覆盖规则**：
- 打开：由覆盖对齐决定（规则和时机见 spec 0006 的「覆盖对齐」一段）。会话 `online` 时，给应当采集但还没有开放段的频道各打开一段，时间是对齐那一刻。全新登录（IDENTIFY）后不直接打开，等紧接着的全量同步对齐；打开开关后也由对齐打开。
- 关闭：网关会话丢失（断线后不能 RESUME）时，在断线时间关闭；关掉开关、频道被删或没权限时，由覆盖对齐关闭，时间是对齐那一刻。RESUME 成功的断线本身不关闭，之后的对齐可能按当前状态关闭，时间同样是对齐那一刻。
- 关闭时上界取 `GREATEST(关闭时间, 起点)`；结果是空范围的段直接删除（返回 `'discarded_empty'`）。
- `close_stale_coverage(account_id)`：collector 启动时调用。关掉这些开放的实时段：频道的 `collector_account_id` 是这个账号、或者为空、或者频道的 `collect_enabled = false`；上界取 `GREATEST(coalesce(last_heartbeat_at, 起点), 起点)`；空的段删除。
- 历史覆盖由第 10 项在任务 `done` 时写入，范围是 `[range_start, range_end)`。

### Value sourcing

| 动作 | 产生或显示的值 | 来源 |
|---|---|---|
| `ingest_message` | `created_at` | 由消息 ID 算出：Discord 纪元 2015-01-01 UTC 加上 `timedelta(milliseconds=id >> 22)`，用整数运算，纯函数 `snowflake_time` |
| `ingest_message` | `guild_id` | `MessageSnapshot.guild_id`（collector 从频道取，复合外键保证一致） |
| `ingest_message` | `author_display_name` | collector 按顺序取：服务器昵称（有成员信息时）→ `global_name` → `username`；补采时作者已离开服务器就没有成员信息，直接从 `global_name` 开始；webhook 用这条消息上的作者名 |
| `ingest_message` | `discord_user.kind` | Discord 消息：有 `webhook_id` → `webhook`，作者 `bot` 为真 → `bot`，否则 `user` |
| `ingest_message` | `reply_to_id` | 类型 19 时取消息引用里的 `message_id`；其他类型一律为空 |
| `ingest_message` | `forwarded` | 消息引用类型是转发时，从转发快照转换成 `ForwardedData`；否则为空 |
| `ingest_message` | `edited_at` | 快照里 Discord 的 `edited_timestamp` |
| `ingest_message` | `source` | 调用方传入（实时事件为 `realtime`，历史任务为 `history`） |
| `ingest_message` | `ingested_at` | 数据库 `now()` |
| `ingest_message` | `version` | 新消息为 1；编辑时为 D.version + 1 |
| `ingest_message` | `message_revision.replaced_at` | 见编辑判断表：S.edited_at，或者 `received_at` |
| `ingest_message` | `attachment.removed_at` | S.edited_at，为空时用 `received_at` |
| `mark_messages_deleted` | `deleted_at` | 实时：collector 收到删除事件的时间（Discord 不提供删除时间）；补采：发现的时间 |
| `upsert_channel` | `position`、`parent_id` | Discord 频道对象；没有就为空 |
| `open_realtime_coverage` | `span` 下界 | 调用方传入的 `at`（见覆盖规则） |
| `close_stale_coverage` | `span` 上界 | `GREATEST(coalesce(last_heartbeat_at, 起点), 起点)` |
| `first_seen_at`（各表） | 第一次入库时间 | 数据库 `now()`，不是 Discord 的创建时间 |
| `discord_account.discord_user_id` | 小号自己的 Discord ID | 第 6 项：collector 第一次登录成功后写入 |
| `account_channel_access` | 能否读取 | 第 7 项：collector 根据 Discord 的权限计算后写入 |
| dashboard 显示「无权限」 | 频道能否读取 | 由 `account_channel_access` 算出，不存在频道上 |

### Key invariants

- 一个 Discord ID 在对应表里最多一行（主键保证），这就是去重的唯一机制。
- `message.version = N` 时，`message_revision` 里正好有该消息版本 1 到 N−1 的行（同一个事务里写两张表，并且锁住消息行）。
- 内容不会倒退：比库里更旧的快照返回 `'stale'`，不写入。
- `deleted_at` 一旦填上就不会改变或清空；被删消息的内容、附件、旧版本、`forwarded` 都不改。
- 补采对账只在任务 `done` 后、只在任务的 ID 范围内进行。
- 服务器、频道、消息、用户都没有删除路径；代码里不出现针对这些表的 `DELETE`。`channel_coverage` 里的空段是唯一允许删除的行。
- `message.guild_id` 永远等于其频道的 `guild_id`（复合外键保证）。
- 每个频道最多一段开放的实时覆盖（部分唯一索引保证）；覆盖段不会是空范围（`CHECK` 保证）。
- 只有可采集类型的频道能打开 `collect_enabled`（`CHECK` 保证）。
- `message.type` 只会是 0 或 19。
- `harvester.db` 的函数从不提交事务。

### Security model

只有你一个人用，不按用户区分权限；dashboard 的访问保护由第 5 项负责。敏感数据只有 token：库里只存密文，主密钥只在 `.env` 的 `TOKEN_ENCRYPTION_KEY`。api 在第 6 项写入 token 时需要加密，所以 api 和 collector 都持有主密钥；只有 collector 解密，api 不读出也不返回密文。`harvester.logging` 现有的过滤规则会把键名含 `token` 的字段替换成 `***`，但只看最外层的键，所以绝不能把 token 放进嵌套结构或异常信息里记日志。采集到的消息是第三方的公开或半公开内容，不涉及 GDPR、PCI 这类合规范围（私信不采）。

### Configuration required

不需要新的环境变量。`TOKEN_ENCRYPTION_KEY` 已在 spec 0001 中定义，加密算法由第 6 项定。

### Critical test scenarios

全部是连真实 PostgreSQL 的集成测试，另有 `snowflake_time`、embed 统一格式和编辑判断表的单元测试。

- Happy path：按顺序写入服务器、分类、频道、作者和一条带附件、embed、回复的消息，再按频道读回，字段完全一致，验证 **AC-2**、**AC-3**
- 缺少上级：频道不存在时 `ingest_message` 返回 `'missing_channel'`，父频道不存在时 `upsert_channel` 返回 `'missing_parent'`，都没有异常，验证 **AC-12**
- 去重：同一条消息先 `realtime` 后 `history`，以及两个并发的 `ingest_message`，最后都只有一行，`source` 是第一次的值，验证 **AC-2**
- 改名：作者改名后再发一条，旧消息的显示名不变，用户表是新名字；webhook 换名字不改用户表，验证 **AC-3**
- 类型：机器人和 webhook 的消息入库且 `kind` 正确；类型 7（加入）和类型 20（斜杠命令回复）返回 `'skipped_type'`，验证 **AC-4**
- 编辑判断表：逐行覆盖表里的每个条件，包括内容改两次得到 `version = 3` 和两行旧版本、embed 展开不加版本、旧快照返回 `'stale'` 且内容不变、编辑时间相同但内容不同仍算编辑，验证 **AC-5**、**AC-6**
- 附件：编辑时拿掉一个附件，它的 `removed_at` 被填上，`version` 不变；链接被更新，验证 **AC-5**
- 删除：批量标记包含已删除和不存在的 ID，三种结果分对；第二次标记不改 `deleted_at`；已删除消息再编辑返回 `'deleted'`；频道改成 `deleted` 后消息的 `deleted_at` 仍为空，验证 **AC-7**、**AC-8**
- 硬删除：`DELETE FROM channel` 删除有消息的频道时被外键拒绝，验证 **AC-8**
- token：api 的 OpenAPI 文档里没有任何响应模型包含 `token` 字段；日志里 `token_ciphertext` 被替换，验证 **AC-9**
- 覆盖：重复打开返回 `'already_open'`；关闭时间早于起点得到 `'discarded_empty'`；模拟崩溃后 `close_stale_coverage` 在心跳时间关闭开放的段，也关掉没有负责账号的段，验证 **AC-10**
- 索引：10 万行加 `ANALYZE` 后 `EXPLAIN` 三个分页查询，验证 **AC-11**
- 迁移：`upgrade head`、`downgrade -1`、再 `upgrade head`，验证 **AC-1**
- 可采集类型：给类型 4（分类）打开开关被 `CHECK` 拒绝；`COLLECTABLE_CHANNEL_TYPES` 和 `CHECK` 一致，验证 **AC-1**
- 转发：转发消息的 `forwarded` 被写入，再次收到时不变，验证 **AC-13**

## Build plan

按 Tracer Bullet：第 3 项只交付 Slice 1 的链路要用的部分（表、模型、写入函数），让第 6、7、8 项可以直接在上面接 Discord 和 dashboard；后面的表由后面的功能各自用只新增的迁移补上。

1. 在 `backend/src/harvester/db/models.py` 写 `discord_account`、`guild`、`channel`、`account_channel_access`、`discord_user`、`message`、`message_revision`、`attachment`、`channel_coverage` 的 SQLAlchemy 模型（`Mapped[...]` 类型标注，满足 pyright strict；所有 `CHECK` 和索引都在 `__table_args__` 里写明名字），并从 `harvester.db` 导出，让 Alembic 能看到，满足 **AC-1**、**AC-12**
2. 生成第一个迁移，手动检查自动生成的结果（部分唯一索引、`DESC` 排序索引、复合外键、`server_default`），`downgrade` 按外键反向顺序删表，满足 **AC-1**、**AC-8**、**AC-10**、**AC-11**
3. 在 `harvester/db/snapshots.py` 写 `frozen` dataclass、`EmbedData`、`ForwardedData`、`DeleteResult`、`COLLECTABLE_CHANNEL_TYPES`，以及纯函数 `snowflake_time`、`normalize_embeds`、`classify_update`（实现编辑判断表），满足 **AC-5**、**AC-12**、**AC-13**
4. 在 `harvester/db/ingest.py` 写 `upsert_guild`、`upsert_channel`、`set_guild_status`、`set_channel_status`、`upsert_user`，满足 **AC-3**、**AC-8**
5. 写 `ingest_message`（插入、并发冲突、编辑判断、附件同步、类型过滤、转发），满足 **AC-2**、**AC-4**、**AC-5**、**AC-6**、**AC-13**
6. 写 `mark_messages_deleted`，满足 **AC-7**
7. 写三个覆盖函数，满足 **AC-10**
8. 确认 `harvester.logging` 现有的敏感字段正则（已匹配 `token`）能盖住 `token_ciphertext`，并加一个测试锁住这一点；api 的响应模型不加任何 token 字段，满足 **AC-9**
9. 写上面列出的集成测试和单元测试（测试框架没装的话，先用 `/test` 装 pytest 并连接测试库），满足 **AC-1** 到 **AC-13**

把 `message_revision` 放进第一次迁移，是在「迁移分批建」上的一个例外：第 10 项补采遇到被改过的消息就要写旧版本（AC-6），而第 10 项排在第 11 项前面；先建这张空表，几乎没有代价。

后续迁移由下面这些功能负责：第 10 项 `history_job`；第 12 项附件的存储字段；第 13 项搜索索引；第 15 项 `keyword`、`keyword_hit`；AI 功能 `message_change` 和向量列。每次都只新增，不修改或删除已有的列。

## Consequences

**Positive**:
- 去重只靠主键，实时、补采、多账号写到同一条消息都不会产生重复，也不需要额外的去重逻辑。
- 平时查询只读 `message` 一张表，dashboard、搜索、导出都看到最新内容。
- `version`、`deleted_at`、`source` 让以后的下单程序能判断「依据的内容是否已经过时」「是不是补回来的旧消息」。
- 覆盖记录让第 10 项可以算出缺口，只补缺的部分，减少对小号的请求。
- 写入规则都写成了表格，collector 和以后的补采走同一个 `ingest_message`，行为一致。

**Negative / tradeoffs**:
- 不存原始 JSON：以后想用一个现在没拆出来的字段（比如表情回应、提及、贴纸），只有新消息才有，旧消息只能重新抓历史。
- 只存类型 0 和 19：机器人用斜杠命令（类型 20）或右键菜单命令（类型 23）回复发出的内容不会被采集。如果以后发现某个信号机器人是这样发消息的，需要放宽 `CHECK`（只新增，不破坏数据），但之前的消息只能重新补采。
- collector 掉线期间的编辑只能拿到最终版本，中间的旧版本永远丢失；掉线期间的删除只能在补采任务完整跑完后发现，删除时间不精确（用 `delete_source = reconciled` 标明）。
- 线程各自一个开关、默认关闭：新开的线程和论坛帖子不会自动采集，容易漏，界面需要提示新线程（推迟到第 8 项之后，见 spec 0006 Follow-up）。
- 变更记录表要等 AI 功能才建：之前发生的编辑和删除没有按顺序的记录，到时只能从 `message` 和 `message_revision` 近似补出来。
- `created_at` 和 `guild_id` 是从别的值复制来的（前者由 ID 算出，后者来自频道，有复合外键保证一致），两者都不会变，但多占一点空间。
- 删除只打标记：每个面向用户的查询都要想清楚是否过滤 `deleted_at`。这是采集器「留痕」的核心需求，不是疏忽。
- 写入规则比单纯的 INSERT 复杂（行锁、编辑判断、附件同步），`ingest_message` 是这个项目最需要测试的函数。
- 消息表不分区，按每天几万条估计，几年内都没问题；如果超过 5000 万行或磁盘使用超过 70%，需要重新评估。

**Neutral**:
- `harvester.db` 新增 `models.py`、`snapshots.py`、`ingest.py` 三个文件；按 spec 0001 的发布规则，改动它们会让 collector 重启一次。
- `harvester.db.raw.COLLECTOR_LOCK_KEY` 保持全局常量，做多账号时再按 `discord_account.id` 区分（spec 0005）。

## Follow-up

- [x] 第 6 项：定 token 加密算法；定账号状态的具体触发规则；判断断线后是 RESUME 还是重新登录，按覆盖规则开关覆盖段；写入 `discord_user_id`。已由 spec 0005 落实。两处调整：advisory lock 改为做多账号时再按账号区分；`account_channel_access` 移到第 7 项写入，已由 spec 0006 落实。
- [x] 第 7 项：打开开关时设置 `collector_account_id` 并打开覆盖段。已由 spec 0006 落实，覆盖段由覆盖对齐打开。
- [ ] 因为线程默认不采，界面上要明显标出新出现的、还没勾选的线程和论坛帖子，最好能一次勾选某个频道下的全部线程。推迟到第 8 项之后，见 spec 0006 Follow-up。
- [ ] 第 10 项：建 `history_job`；按本 spec 的对账规则和覆盖规则实现补采对账、历史覆盖写入和缺口计算。
- [ ] 第 9 项：定心跳频率（不低于每 60 秒，只在 `online` 时写）；磁盘使用率加到心跳或告警里，超过 70% 时提醒。
- [ ] AI 功能的 spec：建 `message_change`，并决定是否从现有数据补出历史变更。
