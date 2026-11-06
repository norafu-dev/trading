# 0008. 实时消息流

**Date**: 2026-11-02
**Status**: In Progress

## Summary

collector 把已开启频道的新消息、编辑和删除写进数据库，dashboard 的频道页几秒内就能看到，刷新后仍在。是否存一条消息，只看这个频道有没有开放的覆盖段（记录“这段时间已采集”的那一行），所以覆盖记录和真实数据始终一致，没勾选的消息也不碰数据库。浏览器收到的 SSE（服务器单向推送）事件只是一个提醒，真正的数据都从同一个消息接口取，所以断线和漏通知都能自己补上。collector 也不再订阅所有服务器，只订阅有开启频道的服务器，流量更小，也更接近官方客户端的行为。

## Requirements

**User stories**:
- 作为你，我想让已开启频道的新消息几秒内出现在 dashboard，刷新后仍在，这样我能跟着看。
- 作为你，我想让被编辑和被删除的消息实时更新，而且旧内容不丢，这样第 11 项上线时已经有完整的记录。
- 作为你，我想按频道切换，并从未读圆点知道哪里有新消息。
- 作为你，我想一眼知道频道是真的安静，还是根本没在采集。
- 作为你，我想让没开启任何频道的服务器不被订阅，同时还能在需要时看到它的线程和论坛帖子。

**Acceptance criteria**:

存储：
- **AC-1**: 频道有开放的实时覆盖段时，频道里的普通消息和回复（类型 0、19，包括小号自己发的）写入 `message`，`source` 是 `realtime`。从 Discord 派发事件到写库提交，正常情况下不超过 1 秒。作者和附件元数据按 spec 0002 写入。
- **AC-2**: 频道没有开放的覆盖段时（没勾选、没权限、已删除、对齐还没做完），这个频道的新消息、编辑和删除都不写库，也不查库。私信和群聊一律不存。
- **AC-3**: 是否存储只用监督循环内存里的名单 `SessionState.open_channels` 判断。每次写覆盖段的事务提交之后才更新这份名单，它和库里开放的实时段保持一致；事务失败时名单不变。
- **AC-4**: 编辑事件走 `ingest_message`，结果遵守 spec 0002 的编辑判断表（真正的编辑写旧版本并让 `version` 加 1，embed 展开不加版本，内容不倒退）。编辑事件的原始载荷没有 `attachments` 键时不同步附件，已有附件不会被标成已移除。库解析失败、没有派发的不完整编辑事件接受丢失。删除和批量删除走 `mark_messages_deleted(source='realtime')`，时间是收到事件的时间，原文保留。
- **AC-5**: RESUME 期间 Discord 重放的消息照常写入，不重复（主键去重）。断线后走全新登录时，覆盖段按断线时间关闭，名单同时清空，等 `FullSync` 对齐后再恢复。
- **AC-6**: 被提及的用户（`message.mentions`）和作者在同一个事务里 upsert 到 `discord_user`，不发任何额外请求。`kind` 规则和 webhook 不更新的规则不变。
- **AC-7**: 会话结束时，队列里已有的当前代号消息事件先按上面的规则写完，再关闭覆盖段，所以覆盖段不会声称采集了没写入的消息。这一步最多 5 秒，不重试；超时或写库失败时记日志（只记丢弃的条数）并继续关闭覆盖段。进程被强杀的情况不在此列（见 Consequences）。

写入失败：
- **AC-8**: 数据库连接类错误（`OperationalError`、`InterfaceError`、`OSError`，或 `DBAPIError.connection_invalidated` 为真）按 0.5、1、2 秒退避重试 3 次，期间保持事件顺序；仍失败就走现有规则退出进程。其余任何异常回滚这一条，记日志（只记 `message_id`、`channel_id`、`error_type`，不记内容），然后跳过这一条继续。把 Discord 对象转成快照时出错同样跳过，不影响连接。

服务器订阅：
- **AC-9**: 客户端用 `guild_subscriptions=False` 启动。READY 之后不再订阅全部服务器，只订阅这些服务器：有 `collect_enabled` 为真且 `status` 不是 `deleted` 的频道，或者 `thread_load_requested_at` 不为空。订阅内容固定是 `typing=True, threads=True`。同一个会话对同一个服务器最多发一次；服务器不在缓存或暂时不可用时不算已订阅，下次对齐再试。
- **AC-10**: 账号在线时打开一个未订阅服务器里的频道，5 秒内发出订阅，这个频道的消息开始入库（包括超过 75,000 人的大服务器，靠 `/check verify` 实测）。
- **AC-11**: 一个服务器的频道全部关掉后，这个会话不主动退订；多出来的消息被名单丢弃。下一个会话（新建 client）只按当时的条件订阅。
- **AC-12**: 管理页里 `enabled_count` 为 0 的服务器显示「加载线程」按钮。点击后 `thread_load_requested_at` 写入当前时间，5 秒内 collector 订阅这个服务器并清空这一列，随后收到的线程写入 `channel`，管理页通过 `channels_changed` 刷新。服务器在这个会话里已经订阅过时不再发指令，直接清空这一列：首次订阅时已经同步过线程，之后事件也一直在收。请求还没被处理时按钮禁用，显示「正在加载」；账号离线时显示「账号上线后加载」。已写入的线程不会因之后退订而消失。

接口：
- **AC-13**: `GET /api/discord/channels/{id}/messages` 支持四种互斥的取法：不带参数取最新一页、`before` 取更早、`after` 取更新、`ids` 取指定的消息。结果总是按 ID 从旧到新，包含已删除的消息，每种都走索引。参数不合法返回 422，频道不存在返回 404 `channel_not_found`。
- **AC-14**: 每条消息带上 spec 0003 `MessageView` 需要的全部字段；响应同时带这一页提及到的用户和频道的名字。所有 Discord ID 在 JSON 里都是字符串，包括 `forwarded` 里的 ID。
- **AC-15**: 新消息产生 SSE 事件 `new_message`，编辑、embed 刷新、删除和因编辑事件才第一次入库的消息产生 `message_changed`，都和写入在同一个事务里 `pg_notify`。载荷只有频道和消息 ID 字符串。`MessageEdited` 的结果是 `unchanged` 时也发 `message_changed`（可能只是拿掉了附件）；`MessageReceived` 的 `unchanged`（RESUME 重放）不发；`stale`、`deleted`、`skipped_type` 不发。

频道页：
- **AC-16**: `/channels?id=<频道ID>` 显示这个频道的消息，打开时停在最新处，往上滚动加载更早的消息（每页 50 条），行为符合 spec 0003 的 AC-7、AC-8。切换频道时列表重新挂载。没有 `id` 时显示「从左边选一个频道」，频道不存在时显示错误块。
- **AC-17**: 当前频道收到 `new_message` 时用 `after` 取增量并合并，同一条消息不会出现两次。正在取数时又来通知，只在当前请求结束后再取一次。`after` 返回 `has_more` 为真时（积压太多），改为重新加载最新一页。别人发消息到页面显示，正常情况下不超过 3 秒。
- **AC-18**: 当前频道收到 `message_changed` 时用 `ids` 取这些消息，就地更新内容、「（已编辑）」和「已删除」标记。比已加载的最新 ID 还新的 ID 触发一次 `after` 取数，比已加载的最早 ID 还旧的 ID 忽略。
- **AC-19**: SSE 重连时和每 30 秒，当前频道重新取最新一页并合并；如果这一页和已有消息没有重叠（中间有缺口），就替换成这一页：丢弃已加载的旧页，`hasOlder` 取这一页的 `has_more`，列表滚到底部。列表为空时直接取最新一页，不用 `after`。所以通知丢失、LISTEN 连接断开或订阅者队列溢出之后，新消息最多晚 30 秒出现。
- **AC-20**: 回复消息上方显示一行引用：被回复者的名字和最多 100 个字符的摘要。原消息不在库里时显示「原消息未采集」，原消息已删除时照常显示并加「已删除」字样。回复消息总是另起一组。
- **AC-21**: `@用户` 显示 `global_name`，没有就用 `username`；`#频道` 显示频道名；查不到的按 spec 0003 显示。
- **AC-22**（已由 [spec 0010](../0010-database-unread-counts.md) 取代：未读改为数据库里的已读位置）: 不在当前查看频道的 `new_message` 让侧栏这个频道出现未读圆点，并让服务器图标栏上它的服务器也出现圆点；打开频道时清除，服务器下没有未读频道时服务器圆点也清除。标签页在后台时，当前打开的频道也算在看，不标未读。未读状态只在页面内存里，刷新后清空。圆点有可读的文字说明（屏幕阅读器读「有未读消息」）。
- **AC-23**: 频道没有在采集时，已存消息照常显示，列表上方有一条状态提示，按这个顺序取第一个符合的：频道已删除、服务器已退出（`guild.status` 是 `left` 或 `deleted`）、已关闭采集、已失去权限、线程已归档、父频道不可用、账号正在重连（`reconnecting`，覆盖段可能还开着，也要提示）、账号不在线（其余不是 `online` 的状态）、正在开始采集（条件都满足但覆盖段还没开）。都不符合时不显示提示。提示随 `channels_changed`、`account_status` 和 30 秒轮询更新。

收口：
- **AC-24**: 新接口没登录返回 401，写操作经过 Origin 检查。日志不含消息内容。`harvester` 里仍然只有 `collector/discord_client.py` 导入 `discord`。新代码通过 `pyright` strict、`ruff`、Biome、`tsc`。

## Decision

**Chosen option**: Option 1: 内存名单判断开放覆盖段，SSE 只做通知，前端统一按接口取数，只订阅有开启频道的服务器

消息事件和现有事件走同一个监督循环队列，一个事件一个事务；api 只读 `message`；浏览器把 SSE 当作“该取数了”的提醒。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `vercel-react-best-practices` (`vercel-labs/agent-skills`, `.claude/skills/vercel-react-best-practices/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`) · `vitest` (`antfu/skills`, `.claude/skills/vitest/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### Data model sketch

只有一个只新增的迁移：

| 表 | 改动 |
|---|---|
| `guild` | 加 `thread_load_requested_at` timestamptz，可空，默认空。api 在点「加载线程」时写入，collector 发出订阅后清空 |

规则变化，不改表结构：`discord_user` 从“只存发言人”改成“发言人和被提及的人”。`upsert_channel`、`upsert_guild` 的冲突更新列不包含新列，所以同步不会把请求冲掉。

不加索引：最新一页、`before`、`after` 走现有的 (`channel_id`, `id` DESC)；`ids` 和被回复消息走主键；附件走 `attachment.message_id`。

### 采集路径（collector）

**新增快照字段**：`MessageSnapshot` 加 `mentions: tuple[UserSnapshot, ...]`，默认空元组（补采也会用）。`ingest_message` 在 `upsert_user(author)` 之后按 ID 排序逐个 upsert，跳过和作者相同的 ID。

**边界函数**（`discord_client.py`）：`snapshot_message(message) -> MessageSnapshot | None`。`message.guild` 为空（私信、群聊、未知服务器）返回 `None`。字段转换按 spec 0002 的 Value sourcing 表。

**新事件**（`events.py`，都带代号）：

| 事件 | 来自库的事件 | 字段 |
|---|---|---|
| `MessageReceived` | `message` | `snapshot`、`at` |
| `MessageEdited` | `raw_message_edit`，用它的 `.message` | `snapshot`、`at` |
| `MessagesDeleted` | `raw_message_delete`、`raw_bulk_message_delete` | `channel_id`、`message_ids`、`at` |

`dispatch` 里同步入队，和现有事件一样不写库，`at` 取注入的时钟。不用 `message_edit`、`message_delete`：它们只在消息还在库的内存缓存里时才触发。

`MessageSnapshot` 再加 `attachments_known: bool = True`。`raw_message_edit` 的 `raw.data` 没有 `attachments` 键时，`snapshot_message` 把它设为假，`ingest_message` 跳过附件同步。这条事件仍按编辑判断表处理内容和 embed。

**监督循环**：

- `SessionState` 增加 `open_channels: frozenset[int] = frozenset()` 和 `subscribed: frozenset[int] = frozenset()`。
- `align_coverage` 改成返回 `CoverageAlignment(plan, open_after)`，`open_after` 就是 `desired`。`_align`、`_write_channels` 和 `_on_channels_wake` 在事务提交后返回带新名单的状态。
- 关闭全部覆盖段（`_on_identify` 的断线关闭）提交后，把 `open_channels` 设成空集合。
- 处理三个消息事件时，先看 `channel_id in state.open_channels`，不在就直接返回。在就开事务写入并在同一事务发通知。

| 事件 | 写入 | 通知 |
|---|---|---|
| `MessageReceived` | `ingest_message(..., 'realtime', at)` | `inserted` 发 `new_message`；`edited`、`embeds_refreshed` 发 `message_changed`；其余不发 |
| `MessageEdited` | 同上 | `inserted`、`edited`、`embeds_refreshed`、`unchanged` 都发 `message_changed`；其余不发 |
| `MessagesDeleted` | `mark_messages_deleted(..., at, 'realtime')` | `marked` 不为空时发 `message_changed`，载荷只含 `marked` |

`MessagesDeleted` 一次最多 100 个 ID，通知载荷在 8000 字节上限之内。

**写入失败**：纯函数 `is_transient_db_error(exc)` 判断 AC-8 的连接类错误。消息写入的重试包在一个函数 `_write_message_event` 里，等待用注入的 `sleep`，测试不真等。退避期间不处理别的事件，这样顺序不变。

**收尾**：`run_session` 的 `finally` 在 `client.close()` 之后、关覆盖段之前，用 `get_nowait` 取出队列里的事件：当前代号的消息事件按同样的门槛写入，不走重试；其他事件按原来的顺序放回队列，行为和现在一样（旧代号由下一个会话过滤）。整个过程用 `asyncio.timeout(5)` 限时，超时或写库失败只记日志和丢弃条数，继续关覆盖段。这一步先于 `_close_coverage`，名单在这段时间保持不变。

### 服务器订阅

- `HarvesterClient` 构造时传 `guild_subscriptions=False`。`chunk_guilds_at_startup=False` 保留。
- `GatewaySession` 协议增加 `async def subscribe_guilds(self, guild_ids: frozenset[int]) -> frozenset[int]`，返回实际发出的服务器 ID。实现是对缓存里找得到且没有 `unavailable` 的每个服务器调用 `guild.subscribe(typing=True, threads=True)`，异常记日志后继续，不传出去。库会把多个服务器合并成一条网关指令发送。
- 查询函数 `subscription_guild_ids(session, account_id)` 返回 AC-9 的服务器集合。这里用 `collect_enabled`，不用覆盖段的 `desired`：已归档的线程没有覆盖段，但服务器仍要订阅，才收得到它重新激活的事件。
- 监督循环在每次覆盖对齐之后（只在 `online` 时）算出 `wanted - state.subscribed`，不为空就调用 `subscribe_guilds`，把返回的 ID 并入 `state.subscribed`。然后清空 `state.subscribed` 里 `thread_load_requested_at` 不为空的服务器（包括这次之前就订阅过的），有清空时发 `channels_changed`。先发订阅再清空：崩溃时最多多订阅一次，不会丢请求。没订阅成功的服务器保留请求，下次对齐再试。
- `state.subscribed` 属于 `SessionState`，新会话从空开始，所以退订就是“下一个会话不再订阅”（AC-11）。同一个 client 内部重新 IDENTIFY 时，库会把已有订阅重新发送，`state.subscribed` 也保留，两边一致。
- `_watch_thread_updates` 同样包一层 `THREAD_LIST_SYNC` 的解析器：原解析器跑完后调用 `_schedule_guild(guild_id)`。库对这个事件不派发新线程的通用事件，不接住的话，订阅后拿到的线程进不了快照。

### 通知通道

`harvester.db.raw` 增加 `MESSAGE_CHANGED_CHANNEL = "message_changed"`，并在 `notifications._EVENT_TYPES` 里加上它。

| 通道 | 载荷 | SSE 事件 |
|---|---|---|
| `new_message` | `{"channel_id": "1", "message_id": "2"}` | `new_message`，`data` 另带 `"type": "new_message"` |
| `message_changed` | `{"channel_id": "1", "message_ids": ["2", "3"]}` | `message_changed`，`data` 另带 `"type": "message_changed"` |

`type` 键是 `Broadcaster` 现有的做法。`routes.py` 里这两个事件都把载荷原样放进 `data`，不再设置 SSE 的 `id`：浏览器的 `Last-Event-ID` 不再有用途。ID 必须是字符串，否则 `JSON.parse` 会丢精度。写一个 `notify_new_message`、`notify_message_changed` 放在 `harvester.db`，写入函数之外的调用方负责调用。

### API surface

都在 `/api/discord` 下，要求登录会话，写操作经过 Origin 检查，错误用 spec 0004 的统一格式。

| Endpoint | Method | Key inputs | Key outputs | Key errors |
|---|---|---|---|---|
| `/channels/{channel_id}` | GET | 路径 ID | `ChannelDetailOut` | 401，404 `channel_not_found` |
| `/channels/{channel_id}/messages` | GET | `before`、`after`、`ids`（逗号分隔，最多 100 个）三选一或都不带；`limit`（1 到 200，默认 50，带 `ids` 时忽略） | `{ messages: MessageOut[], has_more: bool, users: UserBrief[], channels: ChannelBrief[] }` | 401，404 `channel_not_found`，422 |
| `/guilds/{guild_id}/load-threads` | POST | 路径 ID | 202 `{ pending: true }` | 401，404 `guild_not_found`，409 `account_not_found`、`guild_unavailable`（服务器已退出或已删除） |
| `/guilds` | GET（已有） | | `GuildOut` 增加 `thread_load_pending: bool` | |
| `/events` | GET（已有） | | 增加 `message_changed`，`new_message` 的载荷按上文 | |

- ID 参数必须全是数字，取值在 1 到 2^63−1 之间，否则 422。`ids` 有空项时返回 422，重复项去重，去重后超过 100 个返回 422。`before`、`after`、`ids` 同时出现两个以上返回 422。
- `has_more`：多查一行来判断。`after` 表示更新方向还有；其余表示更早方向还有。带 `ids` 时固定为假。`ids` 只返回属于这个频道的消息。
- `load-threads` 写入和 `pg_notify('channel_collect_changed')`、`pg_notify('channels_changed')` 在同一个事务里，幂等。复用已有的 `ChannelsWake`，不新加通道。

`MessageOut`：`id`、`channel_id`、`author_id`、`author_name`、`author_avatar_hash`（可空）、`created_at`、`content`、`embeds`、`forwarded`（可空）、`attachments`（只含 `removed_at` 为空的：`id`、`filename`、`content_type`、`size_bytes`）、`edited_at`（可空，算法见下表）、`deleted_at`（可空）、`reply`（可空）。

`ReplyOut`：`id`、`missing: bool`，以及 `missing` 为假时的 `author_name`、`preview`、`deleted: bool`。

`ChannelDetailOut`：`id`、`guild_id`、`guild_name`、`name`、`type`、`banner_reason`（可空，取值见下）。

`banner_reason` 由后端按 AC-23 的顺序算出，账号状态直接读 `discord_account.status`，所以前端不用再拼条件。取值：`channel_deleted`、`guild_left`、`collect_off`、`no_access`、`thread_archived`、`parent_unavailable`、`account_reconnecting`、`account_offline`、`starting`，都不符合时为空。具体判断：

- `no_access`：没有 `can_read` 为真的访问行。没有访问行也算。
- `parent_unavailable`：频道是线程，父频道不是 `active`，或者父频道没有 `can_read` 为真的访问行。
- `collect_off` 还包括 `collector_account_id` 不是当前账号的情况（包括为空）。
- `starting`：以上都不符合、账号 `online`，但没有开放的实时段。

这些条件和 `desired_channel_ids` 一致，放在 `coverage_plan.py` 旁边并共用同一组测试数据，防止两边不一致。

`UserBrief`：`id`、`name`，只包含这一页消息的正文、转发和 embed 里提及的人。`ChannelBrief`：`id`、`name`。作者名和被回复者的名字已经在消息行上，不放进 `users`。

### Value sourcing

| 动作 | 值 | 来源 |
|---|---|---|
| 存不存 | 频道是否在采集 | `SessionState.open_channels`，来自 `align_coverage` 的 `open_after` |
| 入库 | 消息各字段 | spec 0002 的 Value sourcing，由 `snapshot_message` 转换 |
| 入库 | `received_at`、删除时间 | 事件的 `at`，来自 collector 注入的时钟 |
| 入库 | 被提及的用户 | `message.mentions` |
| 订阅 | 要订阅的服务器 | `subscription_guild_ids` 减去 `state.subscribed` |
| 加载线程 | `thread_load_requested_at` | api 注入的时钟 |
| 加载线程 | `thread_load_pending` | `thread_load_requested_at IS NOT NULL` |
| 消息行 | 作者名、时间、内容、embed、转发 | `message` 的同名列 |
| 消息行 | 头像 | `avatarUrl(author_id, discord_user.avatar_hash)` |
| 消息行 | `edited_at` | `version > 1` 时取 `message.edited_at`，为空就取最新的 `message_revision.replaced_at`；`version = 1` 时为空 |
| 消息行 | 附件 | `attachment`，`removed_at IS NULL` |
| 回复引用 | 名字、删除状态 | 按 `reply_to_id` 查 `message` 主键：`author_display_name`、`deleted_at`，不限频道 |
| 回复引用 | 摘要 | 纯函数：`content` 前 100 个字符；为空时依次用第一个 embed 的标题、「[附件]」、「[转发]」、「[无文字内容]」 |
| 提及 | 用户名字 | 纯函数从这一页的 `content`、`forwarded.content`、embed 描述和字段值里提取 `<@id>`、`<@!id>`（正则排除身份组写法 `<@&id>`，身份组仍按 spec 0003 显示固定文字），批量查 `discord_user`：`global_name`，没有就 `username` |
| 提及 | 频道名字 | 同样提取 `<#id>`，批量查 `channel.name` |
| 频道页提示 | 显示哪一条 | `ChannelDetailOut.banner_reason`，由频道、服务器、访问行、覆盖段和 `discord_account.status` 算出 |
| 频道页 | 账号状态变化时刷新 | `account_status` 事件让频道详情查询失效 |
| 频道页 | 当前频道 ID | `useSearchParams` 的 `id` |
| 频道页 | `now`、`timeZone` | `useNow()`、`Intl.DateTimeFormat().resolvedOptions().timeZone` |
| 未读 | 哪些频道未读 | `new_message` 的 `channel_id`，和当前频道比较 |
| 未读 | 服务器圆点 | 未读频道 ID 经 `useCollected` 的数据映射到 `guild_id` |

### 前端

- **事件总线**：`event-stream.tsx` 增加 `useMessageEvents(listener)`，内部用一个 `ref` 里的订阅者集合，把 `new_message`、`message_changed` 和重连转发给订阅者。这两个事件不写入 React 状态，避免每条消息让整个外壳重新渲染。
- **未读**：新的 `UnreadProvider` 放在 `CollectedShell` 这一层，状态是 `ReadonlySet<string>` 加当前频道 ID。Provider 不读 `useSearchParams`（否则要把整个外壳包进 `Suspense`）；频道页挂载和 `id` 变化时调用 `setCurrentChannel(id)`，卸载时设回空。
- **频道页**：`web/src/app/(dashboard)/channels/page.tsx`，依赖 `useSearchParams` 的部分包在 `Suspense` 里，fallback 是列表骨架。组件放 `web/src/components/channels/`：`channel-page.tsx`（组合和 key 重新挂载）、`channel-status-banner.tsx`、`use-channel-detail.ts`、`use-channel-messages.ts`。
- **消息查询**：`use-channel-messages` 用 `useInfiniteQuery` 处理最新页和 `before`，增量结果用 `setQueryData` 写回。合并是纯函数 `mergeMessages`（`web/src/components/messages/merge-messages.ts`）：按 ID 去重，新的覆盖旧的，按 `BigInt` 排序，单独测试。`users`、`channels` 合并成 `MentionResolver` 用的两张映射表。`refetchOnWindowFocus` 关闭，轮询按 AC-19 手动做，不用 `refetchInterval`（它会重取所有已加载的页）。
- **视图转换**：纯函数 `toMessageView(out)`，放 `web/src/components/messages/to-message-view.ts`。
- **spec 0003 接口的改动**：`MessageView` 增加 `replyTo: ReplyView | null`，`ReplyView = { missing: true } | { missing: false; authorName; preview; deleted }`。`buildRows` 把 `replyTo` 不为空的消息视为组首。`MessageRow` 在组首上方显示引用行，样式按 `web/DESIGN.md`，用 `muted-foreground` 和 13px 字号。`/dev/preview` 的假数据加几条回复。
- **管理页**：工具栏在 `enabled_count` 为 0 时显示「加载线程」（`Button`，`variant="outline"`），文案按 AC-12。论坛和线程是空的时，这个按钮的提示写明“这个服务器还没订阅，线程可能不全”。
- **状态提示文案**：

| 状态 | 文案 |
|---|---|
| 频道已删除 | 这个频道已被删除，下面是之前采集到的消息 |
| 服务器已退出 | 小号已退出这个服务器，下面是之前采集到的消息 |
| 已关闭采集 | 这个频道已关闭采集，新消息不会保存（附「管理采集频道」链接） |
| 已失去权限 | 小号已失去这个频道的读取权限，采集已暂停 |
| 线程已归档 | 这个线程已归档，重新激活后会继续采集 |
| 父频道不可用 | 这个线程的父频道已不可用，采集已暂停 |
| 账号正在重连 | 账号正在重连，恢复后会继续采集 |
| 账号不在线 | 账号不在线，上线后恢复采集（附去 `/settings/discord` 的链接） |
| 正在开始采集 | 正在开始采集，几秒后会收到新消息 |

### Key invariants

- 消息的存储资格只来自 `open_channels`，它只在写覆盖段的事务提交后更新。监督循环是覆盖段唯一的写入方，所以名单不会和库不一致。
- 消息写入、`upsert_user` 和通知在同一个事务里。一个 Discord 事件一个事务。
- collector 不发任何 REST 请求；这一项新增的网关指令只有服务器订阅。
- 接口只读 `message`，不调用 Discord；消息内容的唯一来源是库，SSE 载荷不带内容。
- 编辑和删除只经过 `ingest_message`、`mark_messages_deleted`，不新增写入路径。
- 列表里同一个 ID 最多一条。
- 日志不含消息内容。

### Security model

只有你一个使用者。所有接口要求 spec 0004 的登录会话，写操作经过 Origin 检查，SSE 已有的会话检查不变。消息是第三方的内容，按 spec 0003 的规则渲染，全程不用 `dangerouslySetInnerHTML`，链接经过 `safeUrl`；回复摘要作为纯文字显示，不解析 markdown。不涉及 GDPR、PCI 等合规范围（私信不采）。

### Configuration required

不需要新的环境变量。

### Critical test scenarios

后端用连真实 PostgreSQL 的集成测试，Discord 用假对象；真实账号只在 `/check verify` 里验证。

- 主流程：开启频道，假事件 `MessageReceived` 写入，通知发出，接口读回字段完全一致，verifies **AC-1**、**AC-13**、**AC-14**、**AC-15**
- 门槛：没开启、对齐前、失去权限后的消息都不写库，并且数据库零查询；对齐之后才写入，verifies **AC-2**、**AC-3**
- 名单一致：开关、权限丢失、断线关闭、事务失败的情况下，`open_channels` 都和库里开放的段相同，verifies **AC-3**、**AC-5**
- 编辑和删除：编辑写旧版本；embed 展开不加版本；原始载荷没有 `attachments` 时附件不被标成已移除；批量删除包含不存在的 ID，只通知 `marked`；未开启频道的编辑不写库；编辑事件第一次入库发 `message_changed` 不发 `new_message`，verifies **AC-2**、**AC-4**、**AC-15**
- RESUME：同一条消息收到两次只有一行，只发一次 `new_message`，没有多余的 `message_changed`；IDENTIFY 后名单为空，verifies **AC-5**、**AC-15**
- 提及：被提及的人写入 `discord_user`，接口的 `users` 带上名字，verifies **AC-6**、**AC-21**
- 收尾：队列里还有消息时结束会话，消息写入后才关覆盖段；数据库不可用时 5 秒内结束且覆盖段仍被尝试关闭；其他事件留在队列里，verifies **AC-7**
- 失败：连接错误重试后成功、重试用尽退出；`DataError`（例如内容含 NUL 字符）跳过并继续；快照转换出错不影响后续事件；日志没有消息内容，verifies **AC-8**、**AC-24**
- 订阅：客户端参数是 `guild_subscriptions=False`；只订阅符合条件的服务器；同一个会话不重复；不在缓存的服务器不算已订阅，下次对齐再发；关闭全部频道后不退订，新会话不再订阅；已归档线程所在服务器仍订阅，verifies **AC-9**、**AC-10**、**AC-11**
- 加载线程：接口幂等；每个错误路径；发出订阅后列被清空；已订阅的服务器不发指令也清空；`THREAD_LIST_SYNC` 触发快照；同步不会冲掉这一列，verifies **AC-12**
- 接口：四种取法、边界、`has_more`、互斥参数 422、超过 2^63−1 的 ID 返回 422 而不是 500、`ids` 的空项和重复项、大 ID 字符串不丢精度；`<@&id>` 不进 `users`；`banner_reason` 的每个取值和优先顺序，尤其是父频道失权的线程不会一直显示 `starting`；`EXPLAIN` 走索引无 Sort；`edited_at` 的三种情况；附件过滤；回复的三种状态和摘要回退，verifies **AC-13**、**AC-14**、**AC-20**
- 前端：`mergeMessages` 去重、覆盖、排序；`toMessageView`；单飞取数；`has_more` 时重载；无重叠时替换；替换后 `hasOlder` 和滚动位置；`message_changed` 就地更新、比最新还新的 ID 触发 `after`、比最早还旧的 ID 忽略；每个 `banner_reason` 的文案；未读的增加和清除，后台标签页不标当前频道；`@` 和 `#` 的名字，verifies **AC-16**、**AC-17**、**AC-18**、**AC-19**、**AC-20**、**AC-21**、**AC-22**、**AC-23**
- 权限：新接口没登录 401，错误 Origin 被拒绝，verifies **AC-24**
- 手动（`/check verify`，用真实小号）：端到端延迟；大服务器订阅后能收到消息；不订阅的小服务器是否仍收到消息；自己的角色变化在没有 `member_updates` 时是否仍能收到；用户账号的 `MESSAGE_UPDATE` 是否完整（embed 展开时有没有 `content`、`type`、`attachments`，解析失败的警告日志多不多）；转发消息的字段，verifies **AC-1**、**AC-4**、**AC-10**、**AC-17**

## Build plan

按 Tracer Bullet：先让一条真实消息从 Discord 走到页面，再加粗。第 1 步保持库的默认订阅，确保任何时候都收得到消息；第 4 步才切换订阅方式。

1. 细线的写入：`snapshot_message`、`MessageReceived`、`open_channels` 和 `CoverageAlignment`、门槛判断、`notify_new_message`；同步更新 `tests/db/test_coverage_plan.py`（它把 `align_coverage` 的返回值当作 plan 用），satisfies **AC-1**、**AC-2**、**AC-3**
2. 细线的读取：`GET .../messages`（最新页和 `after`）、`new_message` 载荷改成字符串、`useMessageEvents`、`/channels` 页面、`toMessageView`、`mergeMessages`；同步更新 `tests/api/test_notifications.py`（整数 ID 和 SSE `id` 的断言），satisfies **AC-13**、**AC-14**、**AC-16**、**AC-17**
3. 编辑、删除和可靠性：`MessageEdited`、`MessagesDeleted`、`message_changed` 通道、`ids` 取法、就地更新、重连和 30 秒轮询、`before` 翻页、失败重试和跳过、会话收尾，satisfies **AC-4**、**AC-5**、**AC-7**、**AC-8**、**AC-15**、**AC-16**、**AC-18**、**AC-19**
4. 服务器订阅：迁移加 `thread_load_requested_at`、`guild_subscriptions=False`、`subscribe_guilds`、`subscription_guild_ids`、`THREAD_LIST_SYNC` 钩子、`load-threads` 接口、管理页按钮；测试里假的 `GatewaySession` 加上 `subscribe_guilds`，satisfies **AC-9**、**AC-10**、**AC-11**、**AC-12**
5. 阅读体验：`mentions` 写入和名字映射、回复引用和 `MessageView` 改动、未读圆点、`GET /channels/{id}` 和状态提示，satisfies **AC-6**、**AC-20**、**AC-21**、**AC-22**、**AC-23**
6. 收口：补齐测试，鉴权和 Origin 检查，导出 OpenAPI 并生成前端类型，通过 `ruff`、`pyright`、`pnpm lint`、`pnpm typecheck`、`pnpm build`，satisfies **AC-24**

## Consequences

**Positive**:
- 覆盖记录等于真实数据，第 10 项算缺口时可以信任它。
- 只有一条取数路径，新消息、翻页、重连、补漏都走同一个接口，第 10 项补回来的消息也能直接显示。
- 没勾选的消息不碰数据库；只订阅需要的服务器，流量更小，也更接近官方客户端。
- 编辑和删除从第一条消息起就有记录，第 11 项只需要做查看界面。

**Negative / tradeoffs**:
- 不用库推荐的默认订阅，走的是库文档不推荐的路径。没订阅的服务器线程不全，要靠「加载线程」补；未订阅服务器的角色和频道事件是否完整要实测。
- 订阅指令没有回执。覆盖段打开到大服务器开始推消息之间有一个很短的空档（库的批量发送间隔，verify 时实测），这段时间的消息不会入库，覆盖段却算已采集。
- 开关全关后到下一个会话之前不退订，这段时间流量不会下降。
- 如果用户账号收到的编辑事件其实不完整，库会解析失败并丢掉它们，embed 展开和这些编辑就存不下来。`discord.py-self` 的类型定义认为载荷完整，所以先接受，verify 时实测；问题明显时回到本 spec，改成直接读 `raw.data`。
- 所有已订阅服务器的消息都会先转成快照，再按名单过滤，会多花一些 CPU。还没测到问题，先不优化。
- 每条消息一个事务。按每天几万条的估算足够，但如果一个已开启频道爆发，队列会变长，延迟随之上升，没有批量写入。
- 退避重试期间整个监督循环在等，心跳也会晚一点。
- 跳过的坏消息不会出现在覆盖记录里，只能从日志发现。进程被强杀时，队列里的消息会丢，而 `close_stale_coverage` 按心跳时间关闭，最多可能多算 30 秒。
- 消息列表里已加载的旧消息，如果在 SSE 断线期间被编辑，要到下次重新打开频道才更新（最新一页除外）。
- 关掉开关的频道会从侧栏消失，已存的消息只能通过网址打开。
- 未读状态刷新就没了，也不跨设备。

**Neutral**:
- 发布这一项会让 collector 重启，消耗一次登录额度。
- spec 0006 的「第 8 项上线前覆盖段不可信」这条取舍到此结束。

## Follow-up

- [ ] 更新其他 spec 的说明，留给 `/sync`，因为它们有你还没提交的改动：
  - spec 0001：SSE 不再按 `Last-Event-ID` 补发，改为只通知加前端取数；`new_message` 的 ID 是字符串；事件不带 `id`。
  - spec 0002：`discord_user` 也存被提及的人。
  - spec 0003：`MessageView` 增加 `replyTo`；回复消息总是另起一组（AC-5）。
  - spec 0005 Follow-up：`guild_subscriptions` 已由本 spec 决定。
  - spec 0006 AC-1：库在启动时其实会发订阅指令，现在改为只订阅有开启频道的服务器；未订阅服务器的线程要靠「加载线程」。
- [ ] `/check verify` 用真实小号记录 Critical test scenarios 里手动一项的结果。如果未订阅时自己的权限变化收不到，回来决定是否加上 `member_updates`。
- [x] 关掉开关的频道怎么回去看已存消息，已登记到 scope 的 Deferred。
- [x] 把 spec 0006 Follow-up 里的「新出现的线程提示」登记到 scope 的 Deferred。
