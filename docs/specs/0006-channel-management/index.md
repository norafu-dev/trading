# 0006. 频道管理

**Date**: 2026-11-01
**Status**: In Progress

## Summary

collector 上线后，用 Discord 已经发给它的数据（不额外发任何请求）把小号所在的服务器、频道和线程写进数据库，并算出每个频道你读不读得了。你在 dashboard 的「设置 > 采集频道」页按服务器看频道树，用开关决定采哪些，几秒内生效，不用重启。读不了的频道默认藏起来，可以一键显示，显示时带「无权限」标识。开关打开后，collector 才开始为这个频道记录「已采集的时间段」，真正存消息是下一项（实时消息流）做的。

## Requirements

**User stories**:
- 作为你，我想按服务器、分类看到小号能看到的所有频道，这样我能挑出要采的那几个。
- 作为你，我想点一下开关就让采集开始或停止，不用重启任何进程。
- 作为你，我想一眼分清哪些频道小号读不了，这样我不会白勾。
- 作为你，我想在频道很多的服务器里搜索，并按分类一次开关一组，这样不用逐个点。
- 作为你，我想让新建的频道、被删的频道、权限变化自动反映在页面上，不用手动重连。

**不在这一项里做**：一键开启某个频道下的全部线程，以及「新出现的线程」的醒目提示（spec 0002 Follow-up 提到的），推迟到第 8 项之后，见 Follow-up。

**Acceptance criteria**:
- **AC-1**: 小号全新登录（READY）后 10 秒内（按 50 个服务器、每个 500 个频道估算），它所在的服务器、分类、文字、公告、论坛、媒体、语音、舞台频道和缓存里已有的活跃线程写入 `guild`、`channel`；每个文字频道、公告频道和线程各写一行 `account_channel_access`（`can_read` 和 `checked_at`）。同步全程不发任何 Discord 请求（REST 和网关指令都不发，包括订阅指令），只读 discord.py-self 已缓存的数据，所以大服务器里的活跃线程和论坛帖子可能不全，这是接受的取舍，在 `/check verify` 里实测记录。重复同步不产生重复行，`first_seen_at` 不变。
- **AC-2**: 会话期间的变化在 10 秒内反映到库里：频道新建、改名、移动、删除；线程创建、归档、取消归档、删除；小号加入、被移出或退出服务器；服务器改名；影响小号权限的角色或频道覆盖变化。频道被删只把 `channel.status` 改成 `deleted`，同时把 `parent_id` 指向它的线程一并改成 `deleted`（分类被删不级联到它的子频道，它们只是 `parent_id` 所指的分类消失）；退出服务器只把 `guild.status` 改成 `left`；里面的消息一概不动。同一个服务器的事件按固定 2 秒窗口合并成一次写入。
- **AC-3**: `can_read` 为真，当且仅当小号对这个频道同时有「查看频道」和「读取消息历史」权限，由 discord.py-self 缓存的角色和覆盖在本地算出；服务器的自己成员信息缺失时不写这个服务器的 `account_channel_access`，也不执行「缺席频道写假」，全部沿用旧值。没有行等同于无权限（例外见 AC-6 的「从未同步」）。
- **AC-4**: 页面 `/settings/channels` 左边列出服务器（显示已开启的频道数），右边是选中服务器的树：未分类频道在最上，然后是分类，分类下是频道，线程缩进挂在父频道下面；论坛和媒体频道只作为分组出现，它们的帖子（线程）各自有开关。顺序按 `position`，没有 `position` 的排后面再按名字。选中的服务器放在网址 `?guild=<id>` 里。
- **AC-5**: 读不了的频道默认不显示，工具栏的「显示无权限频道」打开后才显示，每行带锁图标和「无权限」标签，开关禁用；工具栏始终写明「已隐藏 N 个无权限频道」。已开启采集但后来失去权限的频道始终显示，标签是警示色的「已失去权限，采集已暂停」，开关仍可关。
- **AC-6**: 开关打开和关闭走 `PUT /api/discord/channels/{id}/collect`。打开时把 `collect_enabled` 设为真，并把 `collector_account_id` 设为账号 ID；关闭时只把 `collect_enabled` 设为假。打开时返回 409 和固定错误码的情况：没有账号（`account_not_found`）、频道类型不可采集（`channel_not_collectable`）、没有读取权限（`channel_no_access`，指有访问行且 `can_read` 为假，或者没有访问行而且 `channels_synced_at` 不为空；账号从未同步过或换身份后还没重新同步时，没有访问行也允许打开，同步后由覆盖对齐决定是否开段）、频道已删除或服务器已退出（`channel_unavailable`）。已归档的线程允许打开，等线程重新激活后才会开覆盖段。频道不存在返回 404 `channel_not_found`。设置成已经是的值返回 200，不发通知。关闭永远不被 409 挡住，没有账号时关闭也返回 200（没有账号就没有任何已开启的频道）。打开和 collector 同时把 `can_read` 写成假时，结果是「已开启但无权限」：页面显示警示，覆盖段不开，这是可接受的结局。
- **AC-7**: 账号在线时，开关改变后 5 秒内，collector 为这个频道打开或关闭一段实时覆盖，不重启任何进程。通知丢了，30 秒内由轮询补上。覆盖段是否应当打开，由同一个纯函数的结果决定：`collect_enabled` 且 `collector_account_id` 是自己、频道 `status` 是 `active`、服务器 `status` 是 `active`、`can_read` 为真。线程还要求父频道 `status` 是 `active` 且父频道的 `can_read` 为真。账号不在线时，开关只保存，上线（READY 或 RESUMED）后再对齐；对齐只在会话状态是 `online` 时执行，`reconnecting` 期间既不开也不关，覆盖段的关闭仍然按 spec 0005 的断线时间规则。
- **AC-8**: 已开启的频道失去读取权限，或者被删、所在服务器被退出，覆盖段在那一刻关闭，`collect_enabled` 保持不变；权限恢复后覆盖段重新打开。线程同理：父频道失去权限或被删、小号被移出私密线程（`can_read` 写成假）、线程被删或归档时，覆盖段在那一刻关闭。
- **AC-9**: 每个分类行有「全部开启」和「全部关闭」，只作用于这个分类的直接子频道（不含线程）里类型可采集的频道；开启时跳过无权限的频道。接口一个事务完成，返回实际改变的数量和因无权限跳过的数量，界面用 toast 说明。
- **AC-10**: 工具栏的搜索框按名字过滤频道和线程，匹配项的分类和父频道一并保留，不区分大小写，不发请求。没有匹配时显示空状态。
- **AC-11**: 页面显示「上次同步于」，来自 `discord_account.channels_synced_at`，从未同步显示「还没有同步过」。collector 每次全量对账或增量写入完成都会更新它，即使内容没有变化；写入带 `config_version` 校验，所以换身份后一次还在路上的旧写入不会把它写回来。（本项不做手动刷新，事件漏了会在下次全新登录时纠正。）
- **AC-12**: 页面数据与库保持一致：collector 或 api 改了频道数据后，5 秒内页面通过 SSE 事件 `channels_changed` 重新取数；SSE 重连（`onopen`）时三个频道查询一起重新取数；另有 30 秒轮询兜底。开关用乐观更新，请求失败时回滚并用 toast 提示。
- **AC-13**: 侧栏（spec 0003）用真实数据：`GET /api/discord/collected` 返回已开启的频道及其分类和父频道，侧栏按 spec 0003 AC-2 的规则显示；开关改变后 5 秒内侧栏更新。没有任何已开启频道时，空状态提供去 `/settings/channels` 的链接。
- **AC-14**: 账号不在线或停用时页面照常显示、开关可以保存，页面上提示「账号离线时开关会先保存，上线后生效」；没有账号时页面引导到 `/settings/discord`。换身份确认（spec 0005 AC-11）后，`channels_synced_at` 清空，开关保留；重新同步之前允许打开开关（见 AC-6），同步后覆盖对齐才决定是否开段。
- **AC-15**: 新接口没登录返回 401，跨站请求被 Origin 检查拒绝。
- **AC-16**: 开关有可读的名称（`aria-label`「采集 #频道名」），键盘可操作；窄屏（小于 768px）先显示服务器列表，选中后显示频道树并带「返回」按钮。
- **AC-17**: 新增代码通过 `pyright` strict、`ruff`、Biome、`tsc`；只有 `collector/discord_client.py` 导入 `discord`。

## Decision

**Chosen option**: 上线全量对账加事件增量，权限在本地算，开关用 NOTIFY 加轮询对齐覆盖段，管理页放在设置下

collector 每次全新登录和每次增量事件都把缓存里的服务器状态变成不可变快照放进队列，监督循环按序写库；开关由 api 写库并发通知，collector 用一个纯函数比较「应该开的覆盖段」和「现在开着的覆盖段」，差多少补多少。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `shadcn` (`shadcn-ui/ui`, `.claude/skills/shadcn/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### Data model sketch

表结构沿用 spec 0002，只有一个只新增的迁移：

| 表 | 改动 |
|---|---|
| `discord_account` | 加 `channels_synced_at` timestamptz，可空，默认空。collector 每次全量对账或增量写入完成时写入当前时间；确认换身份时清空 |

用到的现有字段：`guild`（`id`、`name`、`icon_hash`、`status`）、`channel`（`id`、`guild_id`、`parent_id`、`type`、`name`、`position`、`collect_enabled`、`collector_account_id`、`status`）、`account_channel_access`（`account_id`、`channel_id`、`can_read`、`checked_at`）、`channel_coverage`。`COLLECTABLE_CHANNEL_TYPES` 常量沿用（0、5、10、11、12）。

`channel` 的 `collect_enabled` 约束已经保证不可采集类型不能打开；api 在写之前先检查并返回 409，不依赖数据库报错。

### 同步模型（collector）

`discord_client.py` 增加两个冻结数据类：`AccessSnapshot(channel_id, can_read)` 和 `GuildState(guild: GuildSnapshot, channels: tuple[ChannelSnapshot, ...], access: tuple[AccessSnapshot, ...])`。边界函数 `snapshot_guild(guild, me)` 把一个 discord.py-self 的 `Guild` 转成 `GuildState`：

- 频道：分类、文字、公告、论坛、媒体、语音、舞台，加缓存里的活跃线程（线程的 `parent_id` 是父频道）。其余类型忽略。
- 权限：对文字频道、公告频道和线程调用 `permissions_for(guild.me)`，`can_read = view_channel and read_message_history`。分类、论坛、语音等不写权限行。`guild.me` 为空时 `access` 为空元组，并带一个 `me_known=False` 标记，同时记一条警告日志；应用端看到这个标记就跳过这个服务器的所有访问行写入，包括「缺席写假」。
- 不可用的服务器（`guild.unavailable`）不放进快照。

新增监督循环事件（`collector/events.py`）。除 `ChannelsWake` 外都带会话代号，旧代号一律丢弃；`ChannelsWake` 和现有的 `Wake` 一样不带代号。`FullSync` 紧跟在 `Ready` 之后由同一个 `dispatch('ready')` 依次放入，所以旧会话的 `FullSync` 会被下一个会话的代号过滤掉。

| 事件 | 触发 | 监督循环的动作 |
|---|---|---|
| `FullSync(states, unavailable_ids)` | READY 之后紧接着放入 | 对每个 `GuildState` 做 `apply_guild_state`；库里 `active` 但既不在 `states` 也不在 `unavailable_ids` 里的服务器改成 `left` |
| `GuildChanged(state)` | 服务器、频道、线程、角色、自己成员的变化事件，以及 `guild_join`、`guild_available` | `apply_guild_state` |
| `GuildRemoved(guild_id)` | 退出或被移出服务器 | `set_guild_status(left)`，把这个服务器所有频道的 `can_read` 写成假 |
| `ChannelRemoved(channel_id)` | 频道或线程被删 | `set_channel_status(deleted)`；同一个事务里把 `parent_id` 等于它的线程也设为 `deleted`（只级联到线程，不级联到分类的子频道） |
| `ThreadArchived(thread_id, archived)` | 线程更新事件里带的线程对象 | 把 `status` 设为 `archived` 或 `active`，不依赖快照（归档线程通常不在缓存里） |
| `ThreadAccessLost(thread_id)` | 小号被移出线程（`thread_member_remove` 且是自己） | 把这个线程的访问行 `can_read` 写成假 |
| `ChannelsWake` | 开关改变的通知 | 只做覆盖对齐 |

**事件合并在 client 侧**：每个服务器自己的计时器，从第一个事件起固定 2 秒窗口（不用尾随 debounce，否则繁忙的服务器会一直被推迟），到期时才从缓存取快照。到期时服务器已经不在缓存里或 `unavailable`，就丢弃这次写入（避免排在 `GuildRemoved` 之后的 `GuildChanged` 把服务器改回 `active`）；`GuildRemoved` 会取消这个服务器挂起的计时器。

`apply_guild_state(session, account_id, state, now)`（`harvester.db.channel_sync`）：一个事务内，按服务器做多行 `INSERT ... ON CONFLICT DO UPDATE`，分三批：分类，非线程频道，线程（不逐行调用现有的 `upsert_channel`，避免每行多次往返；缺父级的行跳过并记日志）。服务器原来是 `left` 的改回 `active`。访问行同样多行 upsert（冲突时更新 `can_read` 和 `checked_at`）。库中已有、快照里没有的非线程频道，其访问行 `can_read` 写成假（缺席表示失去权限，不表示被删，被删只认明确的删除事件）。线程缺席不处理。最后写 `channels_synced_at = now`（带 `config_version` 校验，不发 `discord_account_changed`），即使内容没变也写，并发 `channels_changed`。

**覆盖对齐**：纯函数 `plan_coverage(desired, open_now) -> CoveragePlan(to_open, to_close)`，两个参数都是频道 ID 的 `frozenset`。`desired` 来自一条查询：`collect_enabled`、`collector_account_id` 是自己、频道 `active`、服务器 `active`、存在 `can_read` 为真的访问行；如果频道是线程，还要求父频道 `active` 且父频道的访问行 `can_read` 为真。`open_now` 取所有开着的实时段，不限负责账号（兜住异常数据）。监督循环只在会话状态为 `online` 时执行对齐，时机是：`FullSync`、`GuildChanged`、`GuildRemoved`、`ChannelRemoved`、`ThreadArchived`、`ThreadAccessLost`、`RESUMED`、`ChannelsWake`，以及现有 `HeartbeatDue`（30 秒）里顺带一次，不另开定时器。`reconnecting` 期间不对齐，覆盖段的关闭按 spec 0005 的断线时间规则。对齐用 `open_realtime_coverage` 和 `close_realtime_coverage`，时间取注入的时钟的当前时间（spec 0002 的覆盖规则）；读取 `desired` 和写入在同一个事务里。

`Deps` 增加 `channels_wake: asyncio.Event`，由 `channel_collect_changed` 通知设上；不复用 `deps.wake`，否则一次开关会触发读账号，还会打断退避等待。

**对 spec 0005 的修改**：`_on_ready` 不再调用 `open_account_coverage`，只写 `online`；覆盖段由紧接着的 `FullSync` 对齐后打开。断线和会话结束时关覆盖段的规则不变。`close_account_coverage` 不变。

**通知通道**（`harvester.db.raw` 增加常量，载荷都是 `{}`）：

| 通道 | 谁发 | 谁收 | 含义 |
|---|---|---|---|
| `channel_collect_changed` | api（开关、批量） | collector（和 `discord_account_changed` 同一个锁连接） | 开关变了，对齐覆盖段 |
| `channels_changed` | api（开关、批量），collector（每次写完库） | api 的 `Broadcaster` | 页面重新取数；SSE 事件名 `channels_changed` |

collector 的 30 秒对齐不需要等通知。api 的 `routes.py` `events()` 要为 `channels_changed` 增加专门的输出分支（现在所有不是 `account_status` 的载荷都被当成 `new_message`），`notifications.py` 的 `_EVENT_TYPES` 同样增加。

### API surface

都在 `/api/discord` 下，全部要求登录会话，写操作经过 Origin 检查，错误用 spec 0004 的统一格式。写操作和 `pg_notify` 在同一个事务里。ID 一律用字符串。

| Endpoint | Method | Key inputs | Key outputs | Key errors |
|---|---|---|---|---|
| `/api/discord/guilds` | GET | | `{ synced_at, guilds: GuildOut[] }` | 401 |
| `/api/discord/guilds/{guild_id}/channels` | GET | 路径 `guild_id` | `{ channels: ChannelOut[] }` | 401，404 `guild_not_found` |
| `/api/discord/collected` | GET | | `{ guilds: GuildBrief[], channels: ChannelOut[] }` | 401 |
| `/api/discord/channels/{channel_id}/collect` | PUT | `enabled: bool` | `ChannelOut` | 401，404 `channel_not_found`，409 `account_not_found`、`channel_not_collectable`、`channel_no_access`、`channel_unavailable`，422 |
| `/api/discord/categories/{category_id}/collect` | PUT | `enabled: bool` | `{ changed: int, skipped_no_access: int }` | 401，404 `category_not_found`，409 `not_a_category`、`account_not_found`，422 |
| `/api/events` | GET（已有） | | 新增事件 `channels_changed`，没有数据载荷 | |

- `GuildOut`：`id`、`name`、`icon_hash`、`status`（`active` 或 `left`）、`enabled_count`（`collect_enabled` 为真且 `active` 的频道数）。`left` 的服务器排在最后。
- `ChannelOut`：`id`、`guild_id`、`parent_id`（可空）、`type`、`name`、`position`（可空）、`status`、`collect_enabled`、`collectable`（类型在 `COLLECTABLE_CHANNEL_TYPES` 里）、`can_read`（任何一行访问记录 `can_read` 为真）。
- `GET .../channels` 返回这个服务器 `status` 为 `active` 的分类、文字、公告、论坛、媒体频道和线程，另加 `status` 为 `archived` 但 `collect_enabled` 为真的线程；不返回语音、舞台和 `deleted` 的频道。一个服务器的频道数有 Discord 上限（每服 500），所以不分页；服务器列表不带频道，所以总量不会爆。
- `GET /collected` 返回所有 `collect_enabled` 为真且 `status` 不是 `deleted` 的频道，加上它们的分类、父频道，以及这些频道所属的服务器（`GuildBrief`：`id`、`name`、`icon_hash`）。前端把它转成 spec 0003 的 `buildChannelTree` 输入。
- 分类批量接口只改 `parent_id` 等于该分类、类型在 `COLLECTABLE_CHANNEL_TYPES`、`status` 为 `active` 的频道，且只在值真的变化时才计数。服务器已 `left` 时开启，所有子频道计入 `skipped_no_access`；关闭照常。
- `enabled_count` 把已失去权限但仍开启的频道也算进去（它们仍然是你选的）。
- 论坛和媒体频道下没有可显示的帖子时，分组仍然显示（空的），因为它们的帖子会随事件出现。
- 模型里没有任何 token 字段。

### Value sourcing

| 动作 | 值 | 来源 |
|---|---|---|
| 同步 | `can_read` | 边界函数：`channel.permissions_for(guild.me)` 的 `view_channel and read_message_history`；`guild.me` 缺失时不写 |
| 同步 | 频道 `parent_id`、`position`、`type`、`name` | discord.py-self 缓存里的频道对象，经 `ChannelSnapshot` |
| 同步 | `channels_synced_at` | 注入的时钟当前时间，监督循环写入，带 `config_version` 校验 |
| 同步 | 服务器是否 `left` | 不在 `FullSync.states` 也不在 `unavailable_ids`，或收到 `GuildRemoved` |
| 线程状态 | `archived`、`deleted` | `ThreadArchived` 的 `archived` 标志；`ChannelRemoved` 的级联（`parent_id`） |
| 同步 | 访问行的 `account_id` | 监督循环里读到的账号（`get_account`），数据库最多一个账号 |
| 打开开关 | `collector_account_id` | `get_account()` 的 `id`，没有账号返回 409 `account_not_found` |
| 打开开关 | 能否打开 | 频道 `type`（`COLLECTABLE_CHANNEL_TYPES`）、`status`、服务器 `status`、访问行的 `can_read`、`channels_synced_at`（从未同步时放行） |
| 覆盖对齐 | 应当开着的频道集合 | 上文 `desired` 的查询（线程另看父频道） |
| 覆盖对齐 | 现在开着的集合 | `channel_coverage` 里所有开着的实时段 |
| 覆盖对齐 | 开始和结束时间 | 注入的时钟当前时间（对齐执行的那一刻） |
| 页面「无权限」 | 是否无权限 | `ChannelOut.can_read`，由 `account_channel_access` 算出 |
| 页面「已隐藏 N 个无权限频道」 | N | 当前服务器频道里 `collectable` 为真、`can_read` 为假、`collect_enabled` 为假的数量，前端从已取数据数出来 |
| 页面「已开启 n」 | n | `GuildOut.enabled_count` |
| 页面「上次同步于」 | 时间 | `GET /guilds` 的 `synced_at`，来自 `discord_account.channels_synced_at` |
| 页面频道类型图标和文字 | 类型名 | 前端常量表，按 `ChannelOut.type`（0 文字，5 公告，4 分类，15 论坛，16 媒体，10 到 12 线程） |
| 侧栏频道树 | 分组和顺序 | `GET /collected` 的 `parent_id`、`position`、`name`，按 spec 0003 AC-2 |
| 批量操作结果 | `changed`、`skipped_no_access` | 同一个事务里实际更新的行数和被跳过的行数 |

### Key invariants

- 同步只读 discord.py-self 已有的缓存，不发任何 Discord 请求；只有 `collector/discord_client.py` 导入 `discord`，事件处理器不写库。
- `collect_enabled` 只由 api 改变；collector 只改 `guild`、`channel` 的元数据、`status` 和访问行，从不改开关。
- 会话 `online` 时，覆盖段开着当且仅当 `desired` 里有这个频道；`reconnecting` 期间不对齐，覆盖段按 spec 0005 的断线时间规则处理。失去权限不改开关，只关覆盖段。
- 线程的覆盖资格同时取决于它自己和父频道。
- 频道和服务器从不硬删除，消息不动（spec 0002 AC-8）。
- 频道被判定为 `deleted` 只来自明确的删除事件（以及父频道被删时对线程的级联），缺席只表示失去权限。
- collector 写 `channels_synced_at` 带版本校验，不发账号通知。
- 数据库最多一个账号，所以所有访问行都属于它；换身份时访问行被删（spec 0005），`channels_synced_at` 一并清空。
- 批量操作一个事务，要么全成要么全不成；所有写操作在同一事务里发通知。

### Security model

只有你一个使用者，所有接口要求 spec 0004 的登录会话，写操作经过 Origin 检查。没有新的敏感数据；频道名和服务器名属于你已经能看到的 Discord 内容。不涉及 GDPR、PCI 等合规范围。

### Configuration required

不需要新的环境变量。

### 界面

设计来源：沿用现有 `web/DESIGN.md` 和 shadcn 组件，不引入新的视觉方向。需要新增 shadcn 的 `Switch` 组件（`components/ui/switch.tsx`，用 `shadcn` skill 添加）；其余用现有的 `Badge`、`Button`、`Collapsible`、`Input`、`ScrollArea`、`Skeleton`、`Tooltip` 和 sonner toast。

- **页面** `web/src/app/(dashboard)/settings/channels/page.tsx`。选中的服务器用 `useSearchParams` 读 `?guild=`，依赖它的部分包在 `Suspense` 里，fallback 是同样结构的骨架（静态导出的要求）。
- **组件** 放 `web/src/components/channels/`：`channel-manager.tsx`（组合）、`guild-list.tsx`、`channel-tree.tsx`、`channel-row.tsx`、`toolbar.tsx`，数据 hook 在 `use-guilds.ts`、`use-guild-channels.ts`、`use-set-collect.ts`、`use-collected.ts`。
- **纯函数** `buildManageTree(channels, { query, showNoAccess })` 返回分组后的树和隐藏数量，单独测试；排序规则与 spec 0003 的 `buildChannelTree` 一致，能复用的部分抽出来共用。
- **布局**：宽度不小于 768px 时两栏（左 260px 服务器列表，右边频道树），工具栏在右栏顶部（搜索框、「显示无权限频道」开关与计数、「上次同步于」）。小于 768px 先显示服务器列表，选中后显示频道树，顶部有「返回」。
- **行**：类型图标加名字加开关；分类行是可折叠标题，右侧「全部开启 / 全部关闭」；论坛和媒体频道是没有开关的标题；线程缩进，名字旁加「线程」标签；`archived` 的线程加「已归档」标签。
- **状态**：加载中显示骨架；取数失败显示错误块和「重试」（`toUiError`）；没有账号显示去 `/settings/discord` 的提示；已有账号但没同步过显示「还没有同步过，账号上线后会自动同步」；服务器里没有可显示的频道显示空状态；搜索无结果显示空状态。账号不在线时在工具栏下方显示「账号离线时开关会先保存，上线后生效」。
- **取数**：`["discord-guilds"]`、`["discord-guild-channels", guildId]`、`["discord-collected"]` 三个查询；收到 `channels_changed` 或 SSE 重连时一起失效；`refetchInterval` 30 秒。开关用乐观更新，`onError` 回滚并 toast。
- **入口**：`/settings/discord` 的账号卡片下加「管理采集频道」链接；侧栏「还没有开启采集的频道」的空状态加按钮指向本页。
- **侧栏**：`app-shell` 的数据源从空列表换成 `use-collected`，转成 `buildChannelTree` 的输入；规则不变。

### Critical test scenarios

后端用连真实 PostgreSQL 的集成测试，Discord 用假的缓存对象；真实账号只在 `/check verify` 里手动验证。

- 全量同步：假的两个服务器、分类、文字、论坛帖子、语音；`FullSync` 后各表行数和字段正确，语音和分类没有访问行；再来一次行数不变，`first_seen_at` 不变；全程假客户端记录到零次请求，verifies **AC-1**
- 权限位：`view_channel` 和 `read_message_history` 的四种组合只有同时为真才 `can_read`；`guild.me` 为空时不写访问行且沿用旧值；没有访问行的频道在接口里是 `can_read=false`，verifies **AC-3**
- 增量：频道改名、移动、删除、线程归档和取消归档、服务器改名、退出服务器、重新加入；2 秒内同一服务器的多个事件只写一次；删除和退出后消息仍在，verifies **AC-2**
- 缺席不等于删除：重新同步时某个频道不在快照里，只是 `can_read` 变假，`status` 不变，verifies **AC-2**、**AC-8**
- 开关接口：每个 409 和 404 的路径；幂等；`collector_account_id` 被写入；关闭永远成功；并发开关同一个频道不报错，verifies **AC-6**
- 覆盖对齐：`plan_coverage` 的纯函数表测试；开、关、权限丢失、权限恢复、删除、退出服务器、账号离线再上线后对齐；`ChannelsWake` 之外靠 30 秒轮询也能补上丢失的通知，verifies **AC-7**、**AC-8**
- 分类批量：包含无权限、不可采集、线程、已经开启的子频道，返回的 `changed` 和 `skipped_no_access` 正确，线程不受影响，整个事务回滚，verifies **AC-9**
- 同步时间：每次对账或增量写入都更新 `channels_synced_at`，内容不变也更新；换身份后一次在路上的旧写入不会把它写回来，verifies **AC-11**
- 线程覆盖：父频道失权、被删，线程被移出、删除、归档，覆盖段都关闭；父频道恢复后重新打开，verifies **AC-8**
- 事件合并：同一服务器 2 秒窗口内的多个事件只写一次；到期时服务器已被移出就丢弃，不会把 `left` 改回 `active`；`FullSync` 里不可用的服务器不会被判为 `left`，verifies **AC-2**
- 竞态：打开开关与 collector 把 `can_read` 写假同时发生，结果是已开启但无权限，覆盖段不开，verifies **AC-6**、**AC-7**
- 从未同步或换身份后：没有访问行也能打开；同步后才对齐，verifies **AC-6**、**AC-14**
- 性能：50 个服务器、每个 500 个频道的 `FullSync` 在 10 秒内写完，verifies **AC-1**
- 通知：开关、批量和 collector 写入后 `/api/events` 都有 `channels_changed`，verifies **AC-12**
- 换身份：确认后 `channels_synced_at` 为空，开关保留，verifies **AC-14**
- 权限：每个新接口没登录 401，错误 Origin 被拒绝，verifies **AC-15**
- 前端：`buildManageTree` 的排序、搜索保留祖先、隐藏计数、论坛分组；开关的乐观更新和回滚；`channels_changed` 触发重新取数；窄屏流程；开关的 `aria-label`；各个状态文案，verifies **AC-4**、**AC-5**、**AC-10**、**AC-12**、**AC-14**、**AC-16**
- 侧栏：开启一个频道后出现在侧栏，关闭后消失；没有已开启频道时空状态有链接，verifies **AC-13**
- 边界：测试扫描 `harvester` 包，只有 `collector/discord_client.py` 导入 `discord`，verifies **AC-17**
- 手动（`/check verify`）：用真实小号确认 `guild.me` 和 `permissions_for` 在 `chunk_guilds_at_startup=False` 下可用，大服务器的频道和线程齐全，事件名与 discord.py-self 2.1.0 一致，verifies **AC-1**、**AC-2**、**AC-3**

## Build plan

按 Tracer Bullet：先打通「同步 → 接口 → 页面开关 → 覆盖段」一条细线，再逐段加粗。

1. 迁移：`discord_account.channels_synced_at`；`harvester.db.raw` 增加三个通知通道常量；确认换身份时清空 `channels_synced_at`，satisfies **AC-1**、**AC-14**
2. 边界与同步细线：`snapshot_guild`、`GuildState`、`FullSync` 事件；监督循环处理 `FullSync` 并调用 `apply_guild_state`；`_on_ready` 不再开覆盖段；`plan_coverage` 加对齐，在 `FullSync` 和 `RESUMED` 后执行，satisfies **AC-1**、**AC-3**、**AC-7**
3. 接口细线：`GET /guilds`、`GET /guilds/{id}/channels`、`PUT /channels/{id}/collect`，加 `channel_collect_changed` 通知和 collector 监听，加 30 秒轮询对齐；`/settings/channels` 一个只有服务器列表、频道列表和开关的最小页面，满足「勾选后几秒内生效」，satisfies **AC-5**、**AC-6**、**AC-7**、**AC-12**
4. 增量同步：会话里的各类事件转成 `GuildChanged`、`GuildRemoved`、`ChannelRemoved`（含线程级联）、`ThreadArchived`、`ThreadAccessLost`，client 侧固定 2 秒窗口合并；缺席处理；线程对父频道的依赖；权限丢失和恢复的联动，satisfies **AC-2**、**AC-8**
5. 页面加粗：树的分组与排序、`buildManageTree`、搜索、「显示无权限频道」与计数、分类批量接口和按钮、`channels_changed` SSE 与 `Broadcaster` 事件、乐观更新、各状态、窄屏流程，满足无障碍要求，satisfies **AC-4**、**AC-5**、**AC-9**、**AC-10**、**AC-11**、**AC-12**、**AC-14**、**AC-16**
6. 侧栏接上真实数据：`GET /collected`、`use-collected`、空状态按钮、设置页入口链接，satisfies **AC-13**
7. 测试与收口：补齐上面的测试场景，鉴权和 Origin 检查，导出 OpenAPI 并生成前端类型，通过 `ruff`、`pyright`、`pnpm lint`、`pnpm typecheck`、`pnpm build`，satisfies **AC-15**、**AC-17**

## Consequences

**Positive**:
- 同步不发任何请求，不增加封号风险，也不受限速影响。
- 开关生效不用重启，通知丢了也有轮询兜底。
- 覆盖段由一个纯函数对齐，崩溃、重连、权限变化、账号离线再上线都走同一条路径，容易测。
- 侧栏终于有真实数据，spec 0003 留下的接口缺口补上了。

**Negative / tradeoffs**:
- 归档线程不会主动拉取，管理页看不到它们，直到它们被重新激活。
- 频道在 collector 离线期间被删，不会被发现（缺席只当作失去权限），会一直显示为无权限；需要以后补一个对账手段。
- 默认隐藏无权限频道，你可能一时不知道某个服务器为什么「少了」频道，靠工具栏的计数提示。
- `guild.me` 和 `permissions_for` 在不加载全部成员的模式下是否可靠，要用真实账号验证；不可靠时权限会变得保守（无权限）。

**Neutral**:
- `FullSync` 之前到 READY 之后有一小段时间覆盖段是关着的，对齐后才打开。
- 第 8 项（实时消息流）上线前，覆盖段会声称采集了实际上没存的消息，所以在第 8 项完成前不要把这个工具当作正式采集在用。
- spec 0005 的 `_on_ready` 做小改动，`/sync` 需要把 spec 0002 和 0005 里「第 7 项」的说明标记为已落实。

## Follow-up

- [ ] `/develop` 对照 discord.py-self 2.1.0 确认事件名和 `Guild.me`、`permissions_for` 的行为，并在 `/check verify` 里用真实小号验证（spec 0005 Follow-up 里关于 `guild_subscriptions` 的验证并入这里）。
- [ ] collector 离线期间被删的频道目前发现不了（缺席只当作失去权限），需要时另写一个对账设计。
- [ ] 一键开启某频道下全部线程、「新出现的线程」醒目提示（spec 0002 Follow-up 提到），推迟到第 8 项之后，到时在 scope 里作为新功能登记。
- [ ] `/check verify` 用真实小号确认两个前提：Discord 是否把读不了的频道发给用户账号（决定「缺席算无权限」是否合理，以及 AC-5 的措辞）；不发订阅指令时大服务器的活跃线程和论坛帖子能拿到多少（AC-1）。
- [ ] 把 scope 第 7 项的 `spec` 指向本 spec。
- [ ] 第 8 项开始前确认 spec 0003 对 `/collected` 返回形状的使用。
