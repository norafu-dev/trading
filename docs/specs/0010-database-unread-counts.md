# 0010. 数据库版频道未读数

**Date**: 2026-11-04
**Status**: Accepted

## Summary

频道的未读状态从“浏览器内存里靠 SSE 事件攒出来的圆点”，改成“数据库里记一个已读位置，未读数现算”。这样刷新页面、SSE 断线重连、换一个标签页，圆点和条数都不会丢，也不会和实际消息对不上。侧栏的频道显示未读条数（满 100 条显示 99+），服务器图标仍只显示一个圆点。对只有一个人用的小库来说，成本很低：一张小表加两个接口。这个 spec 取代 spec 0008 的 AC-22。

## Context

spec 0008 的 AC-22 规定未读只存在页面内存里：收到 `new_message` 事件就给频道加圆点，打开频道就清掉，刷新后清空。事件只是“提醒”，没有数据可以对账，所以两种情况会漏圆点。第一，SSE 断开期间到达的消息没有事件，重连后前端发出 `reconnected`，但未读逻辑只处理 `new_message`，直接忽略它。第二，页面刷新或关闭后重开，所有未读都归零。实际使用中的表现是：点进频道能看到采集到的新消息，侧栏却没有圆点，而且时有时无。

数据本身是齐的：每条消息都有按创建时间递增的雪花 ID（Discord 的消息 ID，越大越新），`message` 表上已有 `(channel_id, id desc)` 索引。所以“某个频道比已读位置新的消息有几条”是一次便宜的索引范围查询。这个项目只有一个用户，开启采集的频道最多几十个，查询量和写入量都很小。

不做的后果：圆点继续不可信，用户无法判断“没有圆点”是真的没有新消息，还是页面漏了事件。

## Requirements

**User stories**:
- 作为唯一的使用者，我想让每个频道的未读状态记在服务器上，这样刷新页面或网络抖动后，圆点仍然准确。
- 作为唯一的使用者，我想在侧栏看到每个频道有几条未读，这样能判断先看哪里。

**Acceptance criteria**:
- **AC-1**: 新增表 `channel_read_state`，每个频道最多一行，记录该频道的已读位置（`last_read_message_id`）。迁移时给每个已开启采集、或已有消息的频道写一行，已读位置是当前该频道最新的消息 ID（没有消息则为 0），所以上线那一刻没有任何历史未读。
- **AC-2**: 开启采集时（单个频道开关，或整个分类批量开启），给还没有记录的频道建一行，已读位置是当时最新的消息 ID（没有消息则为 0）；已有记录的频道不改。没有记录的频道按 0 条未读处理。这样新开启采集的频道，从开启那一刻起算未读。
- **AC-3**: `GET /api/discord/unread` 返回所有“已开启采集、状态不是已删除、未读数大于 0”的频道及其未读数。未读数是该频道里 ID 大于已读位置、没有被删除、且不是采集账号自己发的消息的条数，最多数到 100 条就停。没有未读时返回空列表。
- **AC-4**: `POST /api/discord/channels/{channel_id}/read`，带 `message_id`，把已读位置推到“传入值和该频道最新消息 ID 中较小的那个”，并且只往前推，不会退回去。重复请求结果相同。频道不存在返回 404，ID 格式不对返回 422。
- **AC-5**: 侧栏里有未读的频道，名字加粗，并显示未读条数徽标，大于等于 100 时显示 `99+`。服务器图标栏上，只要这个服务器下任一已开启采集的频道有未读，就显示圆点（不显示数字）。屏幕阅读器读到「N 条未读消息」（满 100 条读「99 条以上未读消息」）。线程各自计数，不累加到父频道。
- **AC-6**: 打开一个频道、消息已加载、并且标签页可见时，前端把屏幕上最新一条消息的 ID 上报为已读位置。之后在标签页可见期间新到的消息，合并进列表后同样上报，所以当前频道不会显示未读。标签页在后台时不上报，期间到达的消息算未读，标签页重新可见时再上报一次。
- **AC-7**: 前端在这些时机重新取未读数：收到 `new_message`（1 秒内合并成一次）、SSE 连上或重连、`channels_changed`、窗口重新获得焦点、每 30 秒。刷新页面后未读数保持，断线期间到达的消息在重连后显示出来。
- **AC-8**: 上报已读成功后，对应频道的徽标立即消失，不用等下一次轮询。上报失败时频道保持未读，下一次触发（新消息、重新可见、30 秒轮询）会再试，不弹错误。
- **AC-9**: 页面内存里的未读状态（`markUnread`、`clearUnread` 和靠事件累加的集合）全部移除，未读只来自 `GET /api/discord/unread`。spec 0008 的 AC-22 标成已被本 spec 取代。

## Options considered

### Option 1: 数据库里记已读位置，未读数现算

每个频道一行已读位置，接口用现有索引现数条数。前端用 TanStack Query 缓存未读数，事件和轮询只负责让它重新取。

**Pros**:
- 刷新、断线、多个标签页都一致，状态不会因为漏事件而错。
- 不需要新索引，也不改 `message` 表；数据量按频道数算，不随消息量增长。
- 能直接给出条数，不只是圆点。

**Cons**:
- 要新增一张表、两个接口和一次迁移。
- 每次新消息后前端要多取一次未读数（用 1 秒合并和 100 条封顶压住）。

### Option 2: 保持在内存，只补上 `reconnected` 的处理

收到 `reconnected` 时，重新取各频道最新消息 ID，和“已看过的位置”比较后补圆点。

**Pros**:
- 改动最小，几行代码。

**Cons**:
- 刷新页面仍会丢状态，所以“时有时无”只是少了一部分。
- 前端仍要在内存里维护“已看过的位置”，没有可对账的权威来源，也给不出条数。

### Option 3: 已读位置存在浏览器 localStorage

**Pros**:
- 不用改后端。

**Cons**:
- 每个浏览器、每台设备各算各的，换设备就全部重新未读。
- 隐私窗口或清理站点数据会丢；仍然需要前端自己在每次取数时对账。

## Decision

**Chosen option**: Option 1: 数据库里记已读位置，未读数现算

用一张 `channel_read_state` 表记每个频道的已读位置，由 `GET /api/discord/unread` 现算条数、`POST .../read` 推进位置，前端的圆点和徽标只来自这个接口。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`) · `vitest` (`antfu/skills`, `.claude/skills/vitest/`)

## Rationale

现有问题的根子是“事件是提醒，不是状态”：spec 0008 自己也说事件丢了靠重新取数补上，但未读圆点没有可取的数据，所以没法补。Option 1 把未读变成可查询的状态，和消息列表“事件只提醒，数据从接口取”的做法一致。

数据量很小（一个用户，几十个频道），查询走现有的 `(channel_id, id desc)` 索引，封顶 100 条，所以不需要缓存、计数器或触发器。计数器列看起来省查询，但要在每次插入、删除、编辑和已读时维护，任何一处漏掉就又会出现“对不上”，不值得。已读位置只往前推（用较大值），所以旧标签页或乱序请求不会把已读退回去；位置被限制在频道最新消息 ID 以内，所以一个写错的 ID 不会让频道永远显示已读。

Option 2 修不了刷新丢状态，也没有权威数据；Option 3 在多设备上各算各的。你要的是“可信的未读”，所以选 Option 1。

## Feature design

**Data model sketch**:

| 表 | 字段 | 说明 |
|---|---|---|
| `channel_read_state` | `channel_id` bigint，主键，外键指向 `channel.id` | 每个频道最多一行 |
| | `last_read_message_id` bigint，非空 | 已读位置；未读 = 比它新的消息 |
| | `updated_at` timestamptz，默认 now() | 最后一次推进的时间 |

不需要新索引：按 `channel_id` 主键查，消息数用现有的 `ix_message_channel_id_id`。表结构只通过 Alembic 迁移添加。

**Migration**：建表后回填一次：

```sql
INSERT INTO channel_read_state (channel_id, last_read_message_id)
SELECT c.id, COALESCE(MAX(m.id), 0)
FROM channel c LEFT JOIN message m ON m.channel_id = c.id
GROUP BY c.id
HAVING c.collect_enabled OR MAX(m.id) IS NOT NULL;
```

**State transitions**: 无状态机。已读位置只增不减：`last_read = GREATEST(old, LEAST(message_id, channel 最新消息 ID))`。

**API surface**:

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/discord/unread` | GET | 无 | `channels: [{channel_id: str, count: int}]`，只含 count 大于 0 的频道，count 最大 100 | dashboard 会话（和其他 discord 接口一样） | 401 |
| `/api/discord/channels/{channel_id}/read` | POST | 路径 `channel_id`（字符串 ID）；JSON 体 `message_id`（字符串 ID，必填） | `last_read_message_id: str`（推进后的位置） | dashboard 会话 | 401，404 频道不存在，422 ID 格式不对或超过 2^63−1 |

ID 一律用字符串，和现有消息接口一样，防止浏览器 JSON 丢精度。路由写在 `message_routes.py` 同一个 `/discord` 路由下，错误走项目统一的异常处理器。改完接口后按项目流程重新导出 `openapi.json` 并生成前端类型。

**未读数查询**（只在 `GET /unread` 里用）：对每个 `channel_read_state` 行，连接 `channel`，筛选 `collect_enabled` 且 `status != 'deleted'`，对每个频道用一个带 `LIMIT 100` 的子查询数 `message` 里满足下面条件的行：`channel_id` 相同、`id > last_read_message_id`、`deleted_at IS NULL`、`author_id IS DISTINCT FROM <采集账号的 discord_user_id>`。在应用层丢掉 0 条的频道。采集账号的 `discord_user_id` 还没有值时（NULL），不排除任何作者。

**Value sourcing**:

| Action | Value produced / displayed | Source |
|---|---|---|
| 未读列表 | 每个频道的未读数 | 数据库现算：`message` 表 + `channel_read_state.last_read_message_id` |
| 未读列表 | “自己发的消息”不算 | `discord_account.discord_user_id`（只有一个账号），在查询里读取 |
| 未读列表 | 哪些频道要显示 | `channel.collect_enabled` 和 `channel.status`，和侧栏的可见规则（`build-channel-tree.ts` 的 `isVisible`）一致 |
| 侧栏徽标 | 数字或 `99+` | 接口的 `count`；前端在 count 大于等于 100 时显示 `99+` |
| 服务器圆点 | 该服务器是否有未读 | 前端把有未读的频道 ID 对应到 `channels` 里的 `guildId`，和现有 `unreadGuildIds` 做法一致 |
| 标已读 | 已读位置 | 前端传“屏幕上最新一条消息”的 ID（`useChannelMessages` 的最新消息）；服务器再限制在该频道最新消息 ID 以内 |
| 标已读 | 当前频道和标签页是否可见 | 前端的当前频道（`?id=`）和 `document.visibilityState` |
| 开启采集时建行 | 初始已读位置 | 该频道 `message` 里当时最大的 ID，没有则 0 |

**Key invariants**:
- 已读位置只增不减，且不超过该频道最新消息 ID（由写入语句保证，不靠前端）。
- `channel_read_state` 每个频道最多一行（主键）；没有行等于 0 条未读，所以任何漏建行的路径只会少显示，不会冒出 99+。
- 未读只来自数据库查询，前端不再自己累加。

**开启采集时建行**：在 `db/channels.py` 里加一个函数 `init_read_state(session, channel_ids)`，对没有记录的频道插入 `(channel_id, 当前最大消息 ID 或 0)`，`ON CONFLICT DO NOTHING`。`set_collect` 开启成功后、`set_category_collect` 里 `changed` 非空且是开启时，在同一个事务里调用。`collect_enabled` 只在这两处被改，所以不会漏。

**前端**:
- 新增 `use-unread-counts.ts`：`useQuery`，key 为 `["discord-unread"]`，`refetchInterval` 30 秒，`refetchOnWindowFocus` 打开；返回 `ReadonlyMap<string, number>`（频道 ID 到条数）。
- `UnreadProvider` 保留名字和位置，但内部改成：订阅 `useMessageEvents`，在 `new_message`（合并成 1 秒内一次）和 `reconnected` 时让 `["discord-unread"]` 失效；`event-stream.tsx` 里 `refreshChannels`（也就是 `channels_changed` 和连上时）同时让它失效。内存里的 `Set`、`markUnread`、`clearUnread` 删除。`useSetCurrentChannel` 保留，只用来告诉 provider 当前频道，当前频道且标签页可见时，把这个频道的条数当 0 显示，避免新消息刚到时闪一下。
- `ChannelPage`：`useChannelMessages` 返回的最新消息 ID 变化、并且标签页可见且状态为 `ready` 时，防抖 300 毫秒后调用 `POST .../read`；`visibilitychange` 变为可见时再上报一次。成功后用 `setQueryData` 立即把该频道从未读缓存里去掉，再让它失效确认。失败只记日志，不弹提示。
- `AppShell`、`ChannelSidebar`、`ServerRail` 的 `unreadChannelIds`（Set）改为 `unreadCounts`（Map）；侧栏加徽标，服务器圆点由 Map 推出。
- 请求用 `openapi-fetch` 的 `api` 客户端，错误经现有的 `toApiError` 处理。

**Security model**: 和其他 `/api/discord/*` 接口一样，要有效的 dashboard 会话。没有新的密钥、角色或租户问题；已读位置不含消息内容。ID 参数走和消息接口同样的路径和 2^63−1 上限校验，不会因为超大 ID 返回 500。

**Configuration required**: 无新环境变量。

**Critical test scenarios**:
- 迁移回填：已开启和有消息的频道各一行，位置等于最新消息 ID；没有消息的开启频道位置为 0，verifies **AC-1**
- 开启采集建行：单个开启、分类批量开启各建一行；重复开启不改已有位置；关闭再开启不重置，verifies **AC-2**
- 未读数：已读位置之后的新消息被数；已删除的不数；采集账号自己发的不数；`discord_user_id` 为 NULL 时都数；满 100 条封顶为 100；没开采集或已删除的频道不出现；线程单独计数；没有行的频道不出现，verifies **AC-3**
- 标已读：推进位置；传更小的值不后退；传比最新还大的值被限制在最新；重复请求幂等；不存在的频道 404；非法或超大 ID 422，verifies **AC-4**
- 前端渲染：徽标数字、`99+`、屏幕阅读器文字；服务器圆点由任一子频道推出；线程各自显示，verifies **AC-5**
- 当前频道上报：可见时消息变化后上报最新 ID；后台标签页不上报，变回可见时上报；当前频道不显示未读，verifies **AC-6**
- 刷新时机：`new_message` 合并、`reconnected`、`channels_changed`、窗口焦点、30 秒都触发取数；模拟断线期间来的消息，重连后徽标出现，verifies **AC-7**
- 标已读后立即清除徽标；接口失败时保持未读并在下次触发重试，verifies **AC-8**
- 旧的内存逻辑被删除，没有残留的导出和测试，verifies **AC-9**
- 手动（`/check verify`）：在另一个频道发消息，断开再恢复网络，刷新页面，确认圆点和条数一致，verifies **AC-5**、**AC-7**

## Build plan

按项目的 Tracer Bullet 方式，先打通一条“数据库到侧栏”的最细链路，再加粗。

1. [x] 迁移和模型：新增 `channel_read_state` 和回填；`db/channels.py` 的 `init_read_state`，并在 `set_collect` 和 `set_category_collect` 的开启路径调用，满足 **AC-1**、**AC-2**
2. [x] 未读接口：`GET /api/discord/unread` 和查询，带测试；先不做标已读，这样侧栏就能显示真实条数（数据库里回填过，初始为空），满足 **AC-3**
3. [x] 前端接上未读接口（最细链路）：`use-unread-counts`，`UnreadProvider` 改为从接口取数，`AppShell`、`ChannelSidebar`、`ServerRail` 改用 Map，显示徽标和 `99+`，删除内存逻辑，满足 **AC-5**、**AC-9**
4. [x] 标已读：`POST .../read` 和写入语句（只往前、限制在最新）；`ChannelPage` 上报、可见性处理、立即清除徽标，满足 **AC-4**、**AC-6**、**AC-8**
5. [x] 刷新时机和可靠性：`new_message` 的 1 秒合并、`reconnected`、`channels_changed`、窗口焦点、30 秒轮询，断线重连的测试，满足 **AC-7**
6. 收尾：导出 `openapi.json` 并生成前端类型（`export_openapi` 然后 `pnpm gen:api`）；更新 spec 0008 的 AC-22 和它的 `verify.md`，手动验证清单，满足 **AC-9**

## Consequences

**Positive**:
- 圆点和条数永远和数据一致：刷新、断线、换标签页都不丢。
- 能显示条数，不只是圆点。
- 不改 `message` 表，不加新索引，也没有计数器要维护。

**Negative / tradeoffs**:
- 多了一张表、两个接口，和一条“开启采集时建行”的规则，漏掉会让该频道不显示未读（所以规定没有行等于 0）。
- 每次新消息（最多每秒一次）和每 30 秒，前端会多一次 `GET /unread`。单用户几十个频道下可以忽略，但不是零成本。
- 标签页在后台时，当前频道的新消息会算未读，切回来立刻被标已读。这和旧 AC-22“后台时当前频道不标未读”略有不同。

**Neutral**:
- 多个标签页之间不会实时同步已读（没有新增 NOTIFY 通道），靠窗口焦点和 30 秒轮询对齐，单用户够用。
- 未读数 100 封顶，所以 `99+` 就是“很多”，不是精确数。

## Follow-up

- [ ] 路线图 `docs/scope/scope.md` 里还没有对应的功能行。这件事增强的是第 8 项“实时消息流”，建议放在第 8 项下面，或新增一行并链接本 spec。
- [ ] 做完后在 spec 0008 的 AC-22 加注“已由 spec 0010 取代”，并更新它的 `verify.md` 里关于刷新后清空的那两条。
- [ ] 以后如果要做“全部标为已读”或“稍后再看”，可以复用 `channel_read_state`，现在不做。
