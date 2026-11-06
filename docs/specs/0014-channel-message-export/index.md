# 0014. 频道消息导出

**Date**: 2026-11-06（2026-11-06 更新：AC-17 给 embed 图片加不过期的副本链接）
**Status**: In Progress

## Summary

这个 spec 让你在频道页里把一个频道（或一个线程）里从某条消息到某条消息的记录导出成文件。点「导出」进入选择模式，点两条消息定下起点和终点（也可以一端选「从最早」或「到最新」），操作栏先显示条数和采集缺口提示，再选 CSV 或 JSONL 下载。服务端在一个只读快照里按消息 ID 每批 500 条边查边写，直接流式（一边生成一边发送）给浏览器，不建任务表、不加表结构，一次最多 10 万条。

## Requirements

**User stories**:
- 作为你，我想把某个频道里一段对话（从这条到那条）存成文件，这样能离线留档或交给别的工具和 AI 处理。
- 作为你，我想用表格软件直接打开导出结果，中文不乱码，一眼看懂每条消息、它有没有被改过或删过。
- 作为你，我想要一份信息完整的程序可读文件，里面有每条消息的全部旧版本。
- 作为你，我想在下载之前就知道有多少条、这段时间里有没有没采到的部分。

**Acceptance criteria**:

入口和选择

- **AC-1**: 频道页标题栏有「导出」按钮，只在频道类型是 0、5、10、11、12（文字、公告、公告线程、公开线程、私密线程）时显示；论坛、媒体、分类、语音频道不显示。已关闭采集、已删除的频道照常显示。在线程页导出的是这个线程自己的消息；在普通频道导出不含它下面的线程。
- **AC-2**: 点「导出」进入选择模式，页面底部固定一条操作栏，消息列表底部留出操作栏的高度，不被它盖住。选择模式下点一条消息的任意位置（包括图片、链接、附件、视频和音频）都是选中它，不触发原来的动作（视频和音频上盖一层透明遮罩，挡住原生控件）。第一次点定下一端，第二次点定下另一端；两端按消息 ID 自动排成起点和终点，和点的先后无关；同一条点两次就是只导出这一条；两端都是消息时再点第三条，就以它为新的第一端重新开始。完整的转换规则见 Feature design 的「选择状态」。
- **AC-3**: 操作栏有「从最早」和「到最新」两个按钮。「从最早」让起点开放（从这个频道库里最早的一条开始），「到最新」让终点开放（到下载那一刻库里最新的一条）。快捷按钮只替换它对应的那一端，另一端保留；有一端开放时再点消息，替换的是消息那一端。范围确定的条件：两端都是消息，或者一端是消息、另一端是开放的，或者两端都开放（整个频道）。只有一端时显示「再点一条消息，或选『从最早』『到最新』」（那一端是快捷按钮时显示「再点一条消息，或选另一个快捷按钮」），下载按钮不可用。再次点亮着的快捷按钮取消它。
- **AC-4**: 已加载的消息里，范围内的消息有背景高亮，起点和终点有明显标记。操作栏显示两端的时间（浏览器本地时间）和作者名，开放的一端显示「最早」或「最新」。
- **AC-5**: 点「取消」或按 Esc 退出选择模式并清空选择；切换频道或离开页面也清空。选择不写进网址。点「跳到最新」或列表重新加载时选择保留。下载开始后留在选择模式，方便换一种格式再下一次。起点和终点的标记不改变消息行的高度。

下载前的预览

- **AC-6**: 范围确定后，前端请求预览接口，操作栏显示「共 N 条」（包括已删除的消息），请求中显示「正在统计」。范围改变时重新请求，旧请求的结果丢掉不用。计数封顶在 100001 条（`LIMIT 100001` 的子查询），不扫完整个大频道。
- **AC-7**: 条数超过 100000 时显示「超过 10 万条上限，请缩小范围」（不显示精确条数），下载按钮不可用。下载接口自己也再查一次，超过时返回 422 `export_too_large`。
- **AC-8**: 范围对应的时间段（起点消息的时间到终点消息的时间；开放的起点算到频道最早的消息，开放的终点算到现在）和这个频道的采集缺口有交集时，操作栏显示「这段时间里有 K 段没有采集（共约 X），导出只包含已采集的消息」和一个去 `/history` 补采的链接，不拦下载。X 用现有的 `formatGapDuration` 格式化。缺口来自现有的 `channel_gaps`，它不计算已删除和小号读不到的频道（这些频道本来也补采不了），所以这些频道不显示缺口提示。频道页顶部现有的整个频道的缺口提示（`ChannelHistoryActions`）照旧显示，操作栏只显示和这次范围有交集的部分。
- **AC-9**: 预览请求失败时，操作栏显示错误和「重试」（走统一的 `toUiError`），下载按钮不可用。预览超时（503 `export_timeout`）显示「统计超时，请缩小范围」和「重试」。两端都开放而频道里没有消息时，预览返回 200、`count` 为 0，操作栏显示「频道里没有消息」，下载按钮不可用。

下载

- **AC-10**: 操作栏里选格式 CSV 或 JSONL（默认 CSV）。点「下载」后用一个临时的 `<a href download>` 打开下载网址（网址由前端手动拼，参数是 `start`、`end`、`format`、`tz`；`openapi-fetch` 管不到文件下载），浏览器直接开始下载，频道页不跳走。服务端每批取 500 条，边查边写，内存占用不随总条数增长。
- **AC-11**: 文件名是 `<服务器名>_<频道名>_<起始日期>_<结束日期>.csv` 或 `.jsonl`。日期是第一条和最后一条导出消息的 `created_at` 按请求里的时区换算的 `YYYY-MM-DD`；线程用线程名当频道名。名字里的 `/ \ : * ? " < > |`、控制字符和空白都换成 `_`，连续的 `_` 合并成一个，去掉首尾的 `_`，服务器名和频道名各自最多 30 个字符（按 Unicode 字符数，不切开代理对；整个文件名保持在 255 字节以内）；处理后为空的名字用 `server` 或 `channel` 代替。`Content-Disposition` 用 `attachment`，`filename*` 给 UTF-8 编码的完整名字，`filename` 给纯 ASCII 的后备名 `channel-<频道ID>_<起始日期>_<结束日期>.<扩展名>`。
- **AC-12**: 文件内容是下载请求那一刻的快照（一个只读、`REPEATABLE READ` 的事务），不受导出过程中新到的消息、编辑和删除影响。范围按消息 ID 包含两端，从旧到新排列。已删除的消息照常导出，带 `deleted_at`。开放的终点取快照里最新的一条，所以文件条数可以比预览时多几条。
- **AC-13**: 正文里的 Discord 标记按 spec 0013 AC-6 的规则处理，但保留原来的大小写和空白：`<@123>`、`<@!123>`、`<@&123>`、`<#123>`、`<t:123:R>` 去掉，`<:pepe:123>` 和 `<a:pepe:123>` 只留 `:pepe:`，`</cmd:123>` 变成 `/cmd`。处理范围：正文、转发的正文、旧版本的正文，以及 embed 的标题、描述、字段名和值、作者名、页脚。文件里不带提及的 ID 和名字映射。
- **AC-14**: 文件里所有时间都按请求里的 `tz`（浏览器的 IANA 时区，比如 `Asia/Shanghai`）写成带偏移的 ISO 8601，精确到秒，例如 `2026-11-06T15:30:00+08:00`。例外：embed 里的 `timestamp` 是发消息的人自己写的字段，原样保留。
- **AC-15**: CSV：开头有 UTF-8 BOM（Excel 双击打开中文不乱码），按 RFC 4180 写（行尾 `\r\n`，含逗号、引号或换行的字段加引号），第一行是表头，每条消息一行，内容是最新版本。列和每列的写法见 Feature design 的「CSV 格式」。防公式注入：文字列（`author_name`、`content`、`forwarded`、`embeds`、`attachments`）里第一个字符是 `=`、`+`、`-`、`@`、制表符或回车的单元格，前面加一个 `'`，Excel 打开时不会当成公式执行；ID、时间、版本列不加。JSONL 不做这个处理。
- **AC-16**: JSONL：每行一个 JSON 对象，UTF-8 不带 BOM，行尾 `\n`，中文不转义，所有 ID 是十进制字符串。字段见 Feature design 的「JSONL 格式」；`revisions` 是全部旧版本，按 `version` 从小到大，没有旧版本时是空数组。
- **AC-17**: 附件：每个附件写文件名、类型和大小。已经保存好的（对应的 `media` 行是 `stored`）写链接 `<SITE_ORIGIN>/api/media/<media_id>`（登录 dashboard 后能打开，不会过期）；其他状态链接为空。转发里的附件按 `forwarded_attachment` 行的 `slot` 对上名字，同样处理；它们的类型和大小取 `media.declared_content_type` 和 `media.declared_size_bytes`，没有 `media` 行时为空。CSV 只列没被移除的附件；JSONL 列出全部，被移除的带 `removed_at`。embed 里的图片另有规则：消息自己的 embed（不含转发里的、旧版本里的）的 `image_url`、`thumbnail_url`，如果有对应的 `media` 行（`kind` 是 `embed_image` 或 `embed_thumbnail`，`slot` 等于这个 embed 在消息 `embeds` 里的序号）且状态是 `stored`，就在这个 embed 里加 `image_media_url`、`thumbnail_media_url`，值是 `<SITE_ORIGIN>/api/media/<media_id>`；没有 `stored` 的行（没有行、`pending`、`failed`、`gone` 等）就不加这个字段。原来的 `image_url`、`thumbnail_url` 原样保留，不改。原因：Discord 的图片链接带有效期，过期后打不开，而已保存的副本不会过期。

错误和边界

- **AC-18**: 在开始写文件之前发现的问题返回统一格式的 JSON 错误：未登录 401；频道不存在 404 `channel_not_found`；`start` 或 `end` 不是这个频道自己的消息（包括它的线程里的消息）404 `message_not_found`；`format`、`tz`、ID 格式不对 422 `invalid_export`（`fields` 里给出是哪个参数）；超过上限 422 `export_too_large`；两端都开放但频道里没有消息 422 `export_empty`（论坛、媒体等本身没有消息的频道请求下载时也落在这里，这是预期的）；计数超时 503 `export_timeout`。预览接口做同样的检查，但不检查 `format` 和 `tz`，也不返回 `export_too_large` 和 `export_empty`：超过上限时 `over_limit` 为真，频道没有消息时 `count` 为 0（AC-7、AC-9）。时区校验同时捕获 `ZoneInfoNotFoundError` 和 `ValueError`（比如 `../x` 这样的名字）。
- **AC-19**: 写文件中途出错（数据库错误、查询超时）时，服务端直接断开连接，不补任何结尾，浏览器把这次下载显示为失败。浏览器取消下载时（包括在第一个字节之前就断开），服务端停止查询，结束事务并把连接还给连接池。
- **AC-20**: 导出里每条查询的 `statement_timeout` 是 30 秒；事务的 `idle_in_transaction_session_timeout` 是 5 分钟（下载卡住不动太久时放掉快照）。预览和下载的计数查询超时都返回 503 `export_timeout`。
- **AC-21**: 两个接口都要求已登录的会话。日志每次导出记一行 `export_finished`（或 `export_failed`、`export_aborted`）：频道 ID、格式、条数、字节数、耗时；不记任何消息内容。
- **AC-22**: 把 `clean_text` 拆出 `strip_markup` 之后，`search_text` 的结果和以前完全一样（现有搜索文字测试全部通过，不需要重建索引）。

## Decision

**Chosen option**: Option 1: 频道页选两条消息，api 用键集分页在只读快照里流式输出

在频道页的选择模式里定下两端，预览接口给出条数和缺口，下载接口在一个 `REPEATABLE READ READ ONLY` 事务里按 `(channel_id, id)` 每批 500 条取数，转成 CSV 或 JSONL 行，通过 `StreamingResponse` 直接发给浏览器。不加表，不加依赖。

**Implementation skills**: `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `vitest` (`antfu/skills`, `.claude/skills/vitest/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`)

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Feature design

### 数据模型

不改表结构，没有迁移。用到的现有表和索引：

| 表 | 用到的列 | 怎么用 |
|---|---|---|
| `message` | `id`、`channel_id`、`guild_id`、`author_id`、`author_display_name`、`type`、`content`、`embeds`、`forwarded`、`reply_to_id`、`version`、`created_at`、`edited_at`、`deleted_at` | 按索引 (`channel_id`, `id` DESC) 做范围查询和计数 |
| `message_revision` | `message_id`、`version`、`content`、`embeds`、`replaced_at` | 每批按消息 ID 批量取，只在 JSONL 时取 |
| `attachment` | `id`、`message_id`、`filename`、`content_type`、`size_bytes`、`width`、`height`、`removed_at` | 每批批量取 |
| `media` | `id`、`message_id`、`kind`、`attachment_id`、`slot`、`filename`、`declared_content_type`、`declared_size_bytes`、`status` | 每批取 `kind IN ('attachment', 'forwarded_attachment')` 的行，走 `ix_media_message` |
| `channel`、`guild` | 名字、类型 | 文件名、可导出类型的判断 |
| 采集缺口 | `harvester.db.history.channel_gaps(session, [channel_id])` | 预览里的缺口提示 |

### 选择状态（前端）

选择状态只在组件里，不进网址：

```ts
type ExportEnd = { readonly kind: 'message'; readonly id: string; readonly createdAt: string; readonly authorName: string } | { readonly kind: 'open' }
type ExportSelection =
  | { readonly step: 'none' }
  | { readonly step: 'one'; readonly first: ExportEnd }          // 只点了一条消息
  | { readonly step: 'ready'; readonly start: ExportEnd; readonly end: ExportEnd }
```

状态放在 `ChannelView` 里（它按 `channelId` 换 key，切换频道时自然清空；`MessageList` 会在 `epoch` 或 `center` 变化时重新挂载，所以不能放在 `MessageList` 里），通过 props 传给 `MessageList` 和 `MessageRow` 用来高亮和拦截点击。

纯函数 `selectMessage(selection, msg) -> ExportSelection` 和 `toggleOpen(selection, side: 'start' | 'end') -> ExportSelection` 处理 AC-2、AC-3 的所有转换。下面的 M、A、B 是消息，`open` 是开放的一端，`ready` 里两端都是消息时总是按 ID 排好（A 早于 B）：

| 当前状态 | 点消息 M | 点「从最早」 | 点「到最新」 |
|---|---|---|---|
| `none` | `one(M)` | `one(open 起点)` | `one(open 终点)` |
| `one(A)` | `ready(A 和 M 排序)`；M 就是 A 时 `ready(A, A)` | `ready(open, A)` | `ready(A, open)` |
| `one(open 起点)` | `ready(open, M)` | 取消，回到 `none` | `ready(open, open)` |
| `one(open 终点)` | `ready(M, open)` | `ready(open, open)` | 取消，回到 `none` |
| `ready(A, B)` | `one(M)` | `ready(open, B)` | `ready(A, open)` |
| `ready(open, B)` | `ready(open, M)` | 取消，`one(B)` | `ready(open, open)` |
| `ready(A, open)` | `ready(M, open)` | `ready(open, open)` | 取消，`one(A)` |
| `ready(open, open)` | `ready(open, M)` | 取消，`one(open 终点)` | 取消，`one(open 起点)` |

`ready(open, open)` 时点消息按「替换消息那一端」的规则没有消息端，约定替换终点。`toRangeParams(selection) -> {start?: string, end?: string} | null`，只有 `ready` 时返回值，开放的一端不传参数。`buildExportUrl(channelId, range, format, tz) -> string` 拼下载网址。

### 接口参数

| 参数 | 预览 | 下载 | 说明 |
|---|---|---|---|
| `start` | 有 | 有 | 起点消息 ID（十进制字符串），不传就是开放的起点 |
| `end` | 有 | 有 | 终点消息 ID，不传就是开放的终点 |
| `format` | 无 | 有，必填 | `csv` 或 `jsonl` |
| `tz` | 无 | 有，必填 | IANA 时区名，用 `zoneinfo.ZoneInfo` 校验 |

`start` 比 `end` 大时服务端交换两端，不报错。参数按原始字符串收进来，交给纯函数 `parse_export_params(...) -> ExportParams | ExportParamError` 手动解析，错误转成 422 `invalid_export`（同 spec 0013 的做法，避免返回 `validation_error`）。

### API surface

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/channels/{channel_id}/export/preview` | GET | `start`、`end`（都可选） | `count`（整数，封顶 100001）、`over_limit`（布尔，`count > 100000`）、`range_start`、`range_end`（实际起止消息的 `{id, created_at}`，频道没消息时为 `null`）、`gaps[]`（`{start, end}`，UTC ISO 8601，已经和范围取过交集） | session | 401、404 `channel_not_found`、404 `message_not_found`、422 `invalid_export`、503 `export_timeout` |
| `/api/channels/{channel_id}/export` | GET | `start`、`end`（可选），`format`、`tz`（必填） | 流式文件。CSV：`text/csv; charset=utf-8`；JSONL：`application/x-ndjson; charset=utf-8`；`Content-Disposition` 见 AC-11；`Cache-Control: no-store` | session | 写文件之前：401、404 `channel_not_found`、404 `message_not_found`、422 `invalid_export`、422 `export_too_large`、422 `export_empty`、503 `export_timeout`；写文件之后的错误只能断开连接（AC-19） |

新的项目异常类：`InvalidExportError`、`ExportTooLargeError`、`ExportEmptyError`、`ExportTimeoutError`，放在新的 `harvester/api/export_routes.py`，走现有的统一异常处理器；`ChannelNotFoundError`、`MessageNotFoundError` 复用 `message_routes.py` 里的。

### 下载流程（后端）

1. 解析参数。在路由里用 `contextlib.AsyncExitStack` 打开会话和事务，事务用 SQLAlchemy 的 `execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)`，然后 `SET LOCAL statement_timeout = 30000` 和 `SET LOCAL idle_in_transaction_session_timeout = 300000`。快照在第一条查询时建立，第 2 到 5 步共用。
2. 查频道（不存在 404）和两端消息（必须 `channel_id` 相等，否则 404）。开放的一端用 `min(id)` 或 `max(id)` 补上；两端都开放且频道没有消息时 422 `export_empty`。
3. 封顶计数：`SELECT count(*) FROM (SELECT 1 FROM message WHERE channel_id = :c AND id BETWEEN :start AND :end LIMIT 100001)`，超过 100000 时 422 `export_too_large`；计数超时 503 `export_timeout`。
4. 用第一条和最后一条消息的时间算文件名。第 1 到 4 步出任何错，立刻关掉 `AsyncExitStack` 再抛出项目异常，这些错误返回 JSON。
5. 返回 `StreamingResponse(generator, background=BackgroundTask(stack.aclose))`：`background` 保证浏览器在第一个字节之前就断开、生成器根本没开始跑时，会话也会关掉并还给连接池。生成器里键集分页：`WHERE channel_id = :c AND id > :last AND id <= :end ORDER BY id LIMIT 500`（第一批用 `id >= :start`），`:last` 是上一批最后一条的 ID。每批再按消息 ID 批量取附件、`media` 行（和 JSONL 时的旧版本），交给纯函数转成文本行，`yield` 出去。
6. 生成器用 `try/finally` 加一个「是否写完」的标志区分三种结局：正常走完记 `export_finished`；捕获到 `Exception` 记 `export_failed` 并重新抛出，让连接断开；其他情况（浏览器断开时生成器收到的 `GeneratorExit` 或 `CancelledError`）记 `export_aborted`。`finally` 里关掉 `AsyncExitStack`（重复关闭没有副作用）。asyncpg 的查询被取消后这个连接会被连接池作废，可以接受。

### 纯函数（不碰数据库）

| 函数 | 位置 | 作用 |
|---|---|---|
| `strip_markup(raw) -> str` | `harvester/db/search_text.py` | 只做 Discord 标记处理（AC-13）；`clean_text` 改成调用它再转小写、合并空白（AC-22） |
| `export_filename(guild_name, channel_name, channel_id, first, last, tz, ext) -> ContentDisposition` | `harvester/api/export_format.py` | AC-11 的名字处理和两种文件名 |
| `csv_header() -> str`、`csv_row(message, tz, site_origin) -> str` | 同上 | AC-15，用标准库 `csv` 写进 `io.StringIO` |
| `jsonl_line(message, tz, site_origin) -> str` | 同上 | AC-16，`json.dumps(..., ensure_ascii=False)` |
| `media_link(media, site_origin) -> str \| None` | 同上 | AC-17，只有 `status == 'stored'` 时返回链接；附件和 embed 图片共用 |
| `with_embed_media_urls(embeds, media_rows, site_origin) -> tuple[EmbedData, ...]` | 同上 | AC-17，按 `(kind, slot)` 给每个 embed 加 `image_media_url`、`thumbnail_media_url`，不改入参，返回新对象；CSV 的 `embeds` 列用同一个结果写 `图片:` 行 |
| `clip_gaps(gaps, range_start, range_end) -> tuple[tuple[datetime, datetime], ...]` | 同上 | AC-8，缺口和范围取交集 |

`message` 参数是一个 frozen dataclass `ExportMessage`（消息列加上它的附件、媒体行和旧版本），由数据库层按批组装。

### CSV 格式

列的顺序固定：

| 列 | 内容 |
|---|---|
| `id` | 消息 ID |
| `created_at` | 发送时间（AC-14） |
| `author_id` | 作者 ID |
| `author_name` | `author_display_name`（当时的显示名） |
| `content` | 处理过标记的正文（AC-13） |
| `reply_to_id` | 被回复的消息 ID，没有就空 |
| `forwarded` | 转发的正文；有转发附件时后面每个附件一行，写法同 `attachments` 列；没有转发就空 |
| `embeds` | 每个 embed 依次写作者名、标题、描述、每个字段一行 `字段名: 值`、页脚，空的部分跳过；多个 embed 之间空一行；有已保存副本的图片，在这个 embed 末尾加一行 `图片: 链接` 或 `缩略图: 链接`（链接规则见 AC-17），没有副本就不写，也不写 Discord 的原链接 |
| `attachments` | 每个没被移除的附件一行：`文件名 链接`，没有链接就只写文件名 |
| `version` | 当前版本号 |
| `edited_at` | 最后编辑时间，没有就空 |
| `deleted_at` | 删除时间，没有就空 |

### JSONL 格式

每行一个对象（示例展开写，实际是一行）：

```json
{
  "id": "1300000000000000001",
  "channel_id": "1200000000000000001",
  "guild_id": "1100000000000000001",
  "type": 0,
  "created_at": "2026-11-06T15:30:00+08:00",
  "author": {"id": "1000000000000000001", "name": "nora"},
  "content": "正文",
  "reply_to_id": null,
  "forwarded": {"message_id": "…", "channel_id": "…", "guild_id": "…", "content": "…", "embeds": [], "attachments": []},
  "embeds": [{"title": "…", "description": "…", "fields": [{"name": "…", "value": "…"}], "image_url": "https://cdn.discordapp.com/…", "image_media_url": "https://harvester.example/api/media/43", "...": "其余 EmbedData 字段原样"}],
  "attachments": [{"id": "…", "filename": "a.png", "content_type": "image/png", "size_bytes": 1234, "width": 800, "height": 600, "removed_at": null, "media_status": "stored", "url": "https://harvester.example/api/media/42"}],
  "version": 2,
  "edited_at": "2026-11-06T15:31:00+08:00",
  "deleted_at": null,
  "revisions": [{"version": 1, "content": "旧正文", "embeds": [], "replaced_at": "2026-11-06T15:31:00+08:00"}]
}
```

- `forwarded` 没有转发时是 `null`；它的 `attachments` 每项是 `{"filename", "content_type", "size_bytes", "media_status", "url"}`，按 `attachment_names` 的顺序和 `slot` 对上；`content_type`、`size_bytes` 取 `media.declared_content_type`、`media.declared_size_bytes`；没有 `media` 行时 `content_type`、`size_bytes`、`media_status` 和 `url` 都是 `null`。
- `embeds` 保留 `EmbedData` 的结构，只对文字字段做 AC-13 的处理；embed 里的 `image_url`、`thumbnail_url` 原样保留（可能已过期）。有已保存副本的，同一个 embed 里多一个 `image_media_url` 或 `thumbnail_media_url`（AC-17），没有就不出现这个键。`revisions` 和 `forwarded` 里的 embed 不加。
- `attachments[].media_status` 是 `media.status`，没有 `media` 行时是 `null`。

### Value sourcing

| Action | Value produced / displayed | Source |
|---|---|---|
| 选择模式 | 两端的时间、作者名 | 已加载的 `MessageOut`（`created_at`、作者显示名） |
| 选择模式 | 范围内高亮 | 已加载消息的 ID 和两端 ID 比较 |
| 预览 | 条数 N | `count(*)`，`message` 按 `channel_id` 和 ID 范围，`LIMIT 100001` 封顶 |
| 预览 | 开放一端的实际消息 | 快照里的 `min(id)` 或 `max(id)` |
| 预览 | 缺口段数 K、总时长 X | `channel_gaps` 的结果经 `clip_gaps`；开放终点的“现在”是服务端的当前时间；X 由前端 `formatGapDuration` 格式化 |
| 下载 | 转发附件的类型和大小 | `media.declared_content_type`、`media.declared_size_bytes` |
| 预览、下载 | 上限 100000 | 代码常量 `EXPORT_MAX_MESSAGES` |
| 下载 | 时区 | 请求参数 `tz`，前端取 `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| 下载 | 文件名里的服务器名、频道名 | `guild.name`、`channel.name` |
| 下载 | 文件名里的日期 | 第一条和最后一条消息的 `created_at`，按 `tz` 换算 |
| 下载 | 附件链接的域名 | 现有配置 `site_origin`（`harvester.config`） |
| 下载 | 附件链接的 ID 和是否可用 | `media.id`、`media.status` |
| 下载 | embed 图片副本的链接 | `media` 里 `message_id` 相同、`kind` 为 `embed_image` 或 `embed_thumbnail`、`slot` 等于 embed 序号、`status = 'stored'` 的行的 `id`，域名取 `site_origin`；每批消息一次查出，不逐条查 |
| 下载 | 正文 | `message.content` 经 `strip_markup` |
| 下载 | 旧版本 | `message_revision` |
| 下载 | 删除和编辑标记 | `message.deleted_at`、`message.edited_at`、`message.version` |

### Key invariants

- 只导出 `channel_id` 正好等于路径里频道的消息，不展开线程。
- 下载的整个过程在同一个只读快照里，写入的每一行都来自这个快照。
- 一次导出最多 100000 条，预览和下载两处都检查。
- 后端每次在内存里最多只放一批（500 条消息和它们的附件、旧版本）。
- `strip_markup` 不改变 `search_text` 的结果。

### Security model

只有你一个用户。两个接口都在现有的会话保护下（`require_session`），没有登录返回 401。导出是只读的 `GET`，同源，不改任何数据。文件名里的名字经过 AC-11 的清理，防止响应头注入和路径字符。附件链接指向需要登录的 `/api/media/{id}`，导出文件流出去也不会直接暴露 R2 签名链接。日志不记消息内容。

### Configuration required

不需要新的环境变量。附件链接用已有的 `site_origin`。

### 代码位置

- 后端：`harvester/api/export_routes.py`（两个路由和异常类，挂到 `routes.py`）、`harvester/api/export_format.py`（纯函数）、`harvester/db/export.py`（快照事务、端点解析、计数、按批取数和组装 `ExportMessage`）、`harvester/db/search_text.py`（拆出 `strip_markup`）。
- 前端：`web/src/components/export/`：`export-selection.ts`（选择状态的纯函数）、`use-export-preview.ts`（TanStack Query）、`export-bar.tsx`（操作栏）、`export-button.tsx`；频道页和 `MessageList`、`MessageRow` 接上选择模式（点击拦截、高亮、两端标记）。

### Critical test scenarios

- 正常流程：选两条消息，预览显示正确条数，下载 CSV 和 JSONL，内容按 ID 从旧到新、包含两端、含已删除消息和旧版本，verifies **AC-2**、**AC-6**、**AC-10**、**AC-12**、**AC-15**、**AC-16**
- 选择转换：按「选择状态」转换表逐格测试（先点新的再点旧的、同一条点两次、第三次点重新开始、快捷按钮开关和替换），verifies **AC-2**、**AC-3**
- 选择保留：点「跳到最新」后选择还在，切换频道后清空，verifies **AC-5**
- 公式注入：正文是 `=HYPERLINK(...)`、`-1`、`@x` 的单元格前面有 `'`，ID 列没有，verifies **AC-15**
- 提前断开：在第一个字节之前断开连接，会话被关掉，连接池里没有泄漏，verifies **AC-19**
- 开放的一端和整个频道：只选起点加「到最新」、两端都开放；空频道两端开放返回 `export_empty`，verifies **AC-3**、**AC-18**
- 快照：导出过程中插入新消息和编辑消息，文件内容不变，verifies **AC-12**
- 上限：用测试里缩小的上限确认预览返回 `over_limit`、下载返回 422 `export_too_large`，verifies **AC-7**
- 范围不在本频道：`start` 是它线程里的消息时 404 `message_not_found`，verifies **AC-1**、**AC-18**
- 标记处理和时区：含提及、表情、斜杠命令的正文；`tz=Asia/Shanghai` 和非法时区，verifies **AC-13**、**AC-14**、**AC-18**
- 附件：`stored` 有链接、`pending` 和 `too_large` 没有，移除的附件只在 JSONL；转发附件按 `slot` 对上，verifies **AC-17**
- embed 图片：`stored` 的 `embed_image` 和 `embed_thumbnail` 在对应序号的 embed 里有 `image_media_url`、`thumbnail_media_url`，原 `image_url` 不变；`pending`、没有行、第二个 embed 的图不会串到第一个；CSV 的 `embeds` 列末尾有 `图片: 链接`；转发和旧版本里的 embed 不加，verifies **AC-17**
- 文件名：中文、非法字符、超长名字、空名字，verifies **AC-11**
- 缺口：范围和缺口部分重叠、完全不重叠，verifies **AC-8**
- 中途失败：模拟第二批查询抛错，响应在已写部分后断开，日志记 `export_failed`，verifies **AC-19**
- 未登录：两个接口都返回 401，verifies **AC-21**
- 搜索文字不变：现有 `build_search_text` 测试全部通过，verifies **AC-22**

## Build plan

按 Tracer Bullet：先打通「选两条消息，下载一个 CSV」这一条最细的链路，再逐段加粗。

1. 拆出 `strip_markup`，`clean_text` 改为调用它，跑通现有搜索文字测试，satisfies **AC-13**、**AC-22**
2. 后端最细链路：`export_format.py` 的 `csv_header`、`csv_row`（先只有消息本身的列）、`export_filename`；`db/export.py` 的快照事务、两端校验和键集分页；`GET /api/channels/{id}/export` 只支持两端都是消息的 CSV；导出 OpenAPI 并同步前端类型，satisfies **AC-10**、**AC-11**、**AC-12**、**AC-14**、**AC-15**
3. 前端最细链路：「导出」按钮、选择模式（点两条消息、排序、高亮、取消和 Esc）、操作栏和「下载」，satisfies **AC-1**、**AC-2**、**AC-4**、**AC-5**、**AC-10**
4. 开放的一端：后端补 `min`/`max` 和 `export_empty`；前端「从最早」「到最新」和 `toggleOpen`，satisfies **AC-3**、**AC-18**
5. 预览接口：计数、上限、`clip_gaps` 和缺口；前端显示条数、上限提示、缺口提示和错误状态；下载接口补上限检查，satisfies **AC-6**、**AC-7**、**AC-8**、**AC-9**、**AC-18**
6. 内容加粗：CSV 的 `forwarded`、`embeds`、`attachments` 列；`media_link`（也用于 embed 图片副本，每批一次查出 embed 的 `stored` 行）；JSONL 的 `jsonl_line`、批量取旧版本；前端格式选择，satisfies **AC-13**、**AC-15**、**AC-16**、**AC-17**
7. 失败和运维：超时设置、`export_timeout`、中途出错断开、取消处理、三种日志事件；确认两个接口都要求会话，satisfies **AC-19**、**AC-20**、**AC-21**
8. 测试：后端纯函数单元测试和连真实 PostgreSQL 的集成测试（快照、范围、上限、附件、失败）；前端选择状态纯函数和操作栏的 Vitest；写 `verify.md` 给 `/check verify` 用，satisfies **AC-1** 到 **AC-22**

## Consequences

**Positive**:
- 不加表、不加依赖、不加进程，和现有架构完全一致。
- 文件生成全部是纯函数，好测；以后加别的格式只要再写一个行函数。
- 快照保证文件内部一致，大导出也不会把服务器内存撑满。

**Negative / tradeoffs**:
- 下载是浏览器通过 `<a href download>` 直接打开链接，写文件之前的错误（比如两次请求之间频道被删、会话过期）返回的是 JSON，浏览器会把这段 JSON 当成一个下载文件存下来，或显示下载失败，页面上没有错误提示；靠预览接口提前拦住，这种情况应当很少。
- CSV 文字列里以 `= + - @` 开头的单元格多了一个 `'`，用 Python 等程序读 CSV 时会看到这个前缀；需要干净的原文请用 JSONL。
- 中途出错时只能断开连接，文件被截断；浏览器会显示失败，但如果你手动保留了半截文件，里面没有标记说明它不完整。
- 一次导出在下载期间一直占着一个数据库连接和一个快照；下载很慢时会让 PostgreSQL 的清理（VACUUM）推迟，最多 5 分钟没动静就放掉。
- 18 位的 Discord ID 用 Excel 直接打开会显示成科学计数法并丢失精度；需要精确 ID 时用导入向导把这几列设成文本，或用 JSONL。
- 范围只能用已加载的消息来点；要从很久以前的一条开始，需要先往上滚到那里（或用「从最早」）。
- 不能导出搜索结果；以后需要时另加入口，后端的格式函数可以复用。

**Neutral**:
- `search_text.py` 里多了一个对外的 `strip_markup`，搜索和导出共用同一套标记规则，以后改规则两边一起变（改规则时搜索那边仍要按现有说明重建 `search_text`）。
- 不记录导出历史。

## Follow-up

- [ ] 你提到想把 dashboard 里的 @ 提及显示整个去掉（频道页和搜索页的用户提及渲染，以及接口里为此批量加载的 `users`），这和导出无关，建议单独跑一次 `/architect` 或作为一个小改动 `/develop`。
- [ ] scope 第 14 项原来写的是“把当前筛选结果导出成文件”“CSV 和 JSON”，这个 spec 改成了单个频道按消息范围导出、CSV 和 JSONL；需要的话跑 `/scope` 更新这一行的说明和 Done when。
- [ ] 确认 api 的 Docker 镜像里有时区数据（`/usr/share/zoneinfo`）。`uv.lock` 里的 `tzdata` 只在 Windows 上装；如果镜像里没有，就把 `tzdata` 加成直接依赖，否则 `Asia/Shanghai` 会被当成非法时区。
- [ ] 以后如果要导出搜索结果，复用 `export_format.py` 和 spec 0013 的 `parse_filters`、`search_conditions`，另开 spec。
