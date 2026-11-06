# 0013. 消息搜索与筛选

**Date**: 2026-11-05
**Status**: Accepted

## Summary

这个 spec 给 dashboard 加一个「搜索」页：在库里所有已采集的消息里按关键词找，可以叠加服务器、频道、作者和时间筛选，结果最新的在前，点一条就跳到频道页里它的上下文。中文短词（两个字）也要快，所以不用分词，而是把每条消息的小写文字切成「相邻两个字」存进 PostgreSQL 自带的数组索引（GIN，一种倒排索引），查询先用索引缩小范围，再逐条精确复核，不装任何扩展、不换数据库镜像。筛选条件定成一套固定的网址参数，以后的导出功能原样复用。

## Requirements

**User stories**:
- 作为你，我想在所有采集过的频道里搜一个词（比如「空投」「ETH」），很快看到所有提到它的消息，这样不用一个个频道翻。
- 作为你，我想把搜索限定在某几个服务器、频道或某几个人、某段时间，这样结果少而准。
- 作为你，我想点一条结果就看到它前后的对话，这样能理解上下文。
- 作为你，我想把一次搜索的网址存下来或刷新后不丢，以后导出也用同样的条件。

**Acceptance criteria**:

入口和条件

- **AC-1**: 图标栏新增「搜索」入口，打开 `/search`。所有条件都存在网址里（参数见 Feature design 的「网址和接口参数」），刷新页面、前进后退、复制网址到新标签页都得到同样的条件和结果。
- **AC-2**: 关键词为空并且没有任何筛选时，页面显示提示「输入关键词或选择筛选条件」，不发搜索请求。只有筛选、没有关键词是合法的搜索（比如「某人最近 7 天说的话」）。
- **AC-3**: 关键词语法：空白（Unicode 空白，含全角空格）分隔的多个词必须全部包含；`"..."` 里的内容当成一个整体短语；词或短语前加 `-` 表示排除。细则：
  - 只有出现在一个词开头（或紧跟开头的 `-` 之后）的半角 `"` 才开启短语，词中间的 `"` 是普通字符（`a"b` 是一个词）；全角引号 `“”` 是普通字符。
  - `-"止损 位"` 排除这个短语；`--foo` 排除 `-foo`；词中间的 `-` 是普通字符（`BTC-USDT` 是一个词）。
  - 单独的 `-` 和空引号 `""` 忽略；引号没闭合时从引号到结尾当成一个短语。
  - 每个词和短语先去掉首尾空白、把连续空白合并成一个空格，再转小写；重复的词（包含和排除分别）只算一次。
- **AC-4**: 关键词限制：整个输入框最多 200 个字符；去重后最多 10 个词（包括排除词）；每个词或短语（按 AC-3 修剪和合并空白之后）至少 2 个字符（按 Unicode 字符数，一个汉字算一个），排除词也一样（`-a` 报错）；只有排除词时，必须同时有服务器、频道、作者或时间中的至少一个筛选。违反时接口返回 422 `invalid_query`，`fields` 里给出原因（`too_long`、`too_many_terms`、`term_too_short`、`exclude_only`，见 Feature design 的「错误格式」）；前端在发请求之前就用同一套规则在输入框下面提示具体原因，不发请求。

匹配规则

- **AC-5**: 命中规则是不分大小写的子串包含：搜「btc」命中「BTCUSDT」，搜「空投」命中「新空投活动」。不做分词、不做全半角或简繁转换。`%`、`_`、`\` 当普通字符。
- **AC-6**: 被搜的文字是每条消息的 `search_text`：正文、转发消息的正文、embed 的标题、描述、字段名和值、作者名、页脚，转发消息的 embed 和附件名，以及这条消息所有附件的文件名（包括编辑时被拿掉的）。正文里的自定义表情 `<:pepe:123>` 只留 `:pepe:`，斜杠命令 `</cmd:123>` 变成 `/cmd`，用户提及 `<@123>`、身份组提及 `<@&123>`、频道提及 `<#123>`、时间戳 `<t:123:R>` 去掉。旧版本（`message_revision`）不参与搜索，只搜当前内容。
- **AC-7**: 库里所有频道的消息都能被搜到，包括已关闭采集和已删除的频道。已删除的消息照常出现在结果里，带现有的删除标记和删除时间。

筛选

- **AC-8**: 服务器筛选和频道筛选共用一个选择器：按服务器分组、可以按名字搜索，只列至少有一条消息的服务器和频道（父频道或论坛自己没有消息但它的线程有，也列出来），已关闭采集、已删除的频道带标记。排序：服务器按名字；服务器内按所在分类的 `position`（没有分类的在最前），再按频道自己的 `position`；线程排在父频道下面，按 ID 从新到旧。分类和语音频道没有消息，自然不出现。可以整个服务器勾选（发 `guild`，不展开成频道 ID），也可以勾选单个频道或线程。选中一个频道时，它下面的线程和论坛帖子一起算进去（只展开线程，不展开分类）。勾选的频道超过 200 个时，前端拦住并提示「频道太多，请改选整个服务器」。服务器和频道之间是「或」：消息属于任一选中的服务器，或任一选中的频道（含其线程），就算符合。
- **AC-9**: 作者筛选：输入名字联想，不分大小写的子串匹配 `discord_user` 的 `username` 和 `global_name`（`%`、`_` 当普通字符），只列至少有一条消息的作者（包括 webhook），最多 20 个候选；可多选（最多 20 人），多个作者之间是「或」。已经选满 20 人时不再添加，提示「最多选 20 人」。结果卡片里的作者名不可点击（2026-11-05 决定不做「点作者名加入筛选」）。刷新后已选作者的名字能从网址里的 ID 还原，库里找不到的 ID 直接丢掉。
- **AC-10**: 时间筛选：快捷项「今天」「最近 7 天」「最近 30 天」和「自定义」（两个浏览器本地时间输入，开始包含、结束不包含，可以只填一边；不填结束就是没有上限，开始在未来是合法的，结果为空）。快捷项在网址里存成相对值，刷新后仍是「最近 7 天」；「今天」按浏览器本地时区的零点算。网址里 `range` 和 `from`/`to` 同时出现时，以 `from`/`to` 为准。早于 Discord 纪元（2015-01-01）的时间按纪元算。开始不早于结束时接口返回 422 `invalid_filters`，前端在输入时就提示。
- **AC-11**: 不同种类的条件之间是「并且」（关键词、服务器或频道、作者、时间同时满足）。

结果

- **AC-12**: 结果按消息 ID 从新到旧，每页 50 条，用 `before` 游标翻页，滚到底自动加载下一页，直到没有更多。每条结果是独立的完整消息卡片（复用现有 `MessageRow`，总是传 `groupStart: true`，所以每条都显示头像和作者，不和上一条合并；没有日期分隔线和「新消息」分隔线；回复摘要和现在一样不能点），正文里的用户和频道提及显示成名字。卡片头部显示服务器名、频道名（只有线程显示「父频道 › 线程」，普通频道不显示分类）、频道已关闭采集或已删除的标记，以及「跳到上下文」链接。
- **AC-13**: 结果里的命中词（不含排除词）在正文、embed 的标题和描述及字段值、转发内容和附件名的显示文字里用 `<mark>` 高亮，不分大小写。只在同一个渲染出来的文字节点里匹配。这几种情况不高亮，是可以接受的：被 markdown 格式拆开的词（`**B**TC`）；显示时被转换掉的部分（比如表情名）；作者名、embed 作者、页脚、字段名；只出现在已移除附件文件名里的命中（卡片不显示已移除的附件）。短语里的空格匹配显示文字里任意长度的连续空白。
- **AC-14**: 结果顶部显示命中总数，最多数到 1000，超过显示「1000+ 条」。总数单独请求，不阻塞第一页；总数查询超时时不显示总数，结果照常显示。
- **AC-15**: 结果是查询那一刻的快照，不随新消息、编辑、删除实时变化。顶部有「刷新」按钮，点击后从第一页重新查（相对时间也重新计算）。同一次翻页过程中时间范围不变（第一页时就把相对时间换算成固定时间）。
- **AC-16**: 状态：第一页加载中显示骨架；第一页请求失败显示错误块和「重试」（走统一的 `toUiError`）；后面的页失败时，已加载的结果保留，在列表底部显示错误和「重试」；没有结果显示「没有找到符合条件的消息」；宽度小于 768px 时筛选收进一个「筛选」按钮打开的面板，结果占满宽度。

跳到上下文

- **AC-17**: 「跳到上下文」打开 `/channels?id=<消息所在频道>&m=<消息ID>`。频道页以这条为中心加载：它之前 25 条、它本身（已删除的消息也照常显示）、它之后 25 条，滚动到它并居中，背景高亮约 2 秒后淡出（系统开启「减少动画」时不淡出，2 秒后直接取消高亮）。向上、向下滚动都能继续加载。已关闭采集或已删除频道的消息也能跳（频道页照常显示现有的状态横幅）。
- **AC-18**: 这条消息不在这个频道里（或不存在）时，频道页显示提示「找不到这条消息，已显示最新消息」，按普通方式加载最新一页，并从网址去掉 `m`。
- **AC-19**: 以某条为中心浏览、还没加载到最新消息时（`has_newer` 为真）：不自动贴底；实时推来的新消息不追加到列表，不触发「缺口时整页换成最新」，30 秒对账也不跑；已加载消息的编辑和删除照常按 `ids=` 刷新；补采完成后的重取按当前的中心重新取；不上报已读（当前频道的侧栏未读徽标仍按现有规则隐藏，其他频道的徽标照常更新）；底部显示「回到最新」。向下滚动加载，直到某次 `after` 返回 `has_more` 为假，就算到了最新（第一次加载时 `has_newer` 已经为假也一样），之后恢复普通行为（贴底、追加实时消息、对账、按现有规则标记已读）。点「回到最新」时重新加载最新一页，并从网址去掉 `m`。

索引和写入

- **AC-20**: 每条消息新插入、内容或 embed 改变、或附件同步之后（实时、历史、编辑、embed 刷新），`search_text` 在同一个事务里、在附件同步之后按 AC-6 的规则重新算好并写入。`ingest_message` 因为消息已删除、内容过时、类型不存或频道不存在而不写时，以及只更新 `edited_at` 或完全没变化、也没带附件信息时，`search_text` 不变（还是 `NULL` 的旧行留给补建）。
- **AC-21**: 含包含词的搜索固定走「GIN 索引和其他条件的索引取交集」的位图计划：集成测试在同一个事务里先执行和生产相同的 `SET LOCAL` 设置，再对带绑定参数的同一条语句做 `EXPLAIN`，断言计划里用到 `ix_message_search_bigrams`，并且没有对 `message` 的 `Seq Scan` 和主键 `Index Scan`。两个汉字的词和三个字母的英文词各测一次。
- **AC-22**: 已有消息的 `search_text` 由 api 启动后在后台自动补建：每批 1000 条，按 ID 从新到旧，批与批之间停 0.2 秒；补完后每 10 分钟再检查一次有没有漏网的（发布期间旧版 collector 写入的行）。补建和 collector 同时写同一条消息时不会互相覆盖（取批时行锁加 `SKIP LOCKED`，写入时只写 `search_text IS NULL` 的行）。api 重启后从剩下的继续。还没补建的消息不会出现在含任何关键词（包括只有排除词）的搜索结果里；只用筛选时照常出现。
- **AC-23**: 补建没完成时，搜索页顶部显示「搜索索引补建中，结果可能不全（还剩 N 条）」，N 来自 `GET /api/search/status`。页面打开期间每 30 秒刷新一次（没有任何条件时也显示），N 为 0 时横幅消失并停止刷新。

失败和安全

- **AC-24**: 搜索查询超过 10 秒就取消（PostgreSQL 的 `query_canceled`，SQLSTATE `57014`），返回 503 `search_timeout`，不是 500，前端显示「这次搜索太慢了，试着加上频道、作者或时间筛选」和「重试」。总数查询超过 5 秒取消，按 AC-14 处理。
- **AC-25**: 所有新接口都要求已登录的会话（`require_session`），未登录返回 401。日志不记关键词原文和消息内容，只记词数、筛选种类、结果条数和耗时。
- **AC-26**: 筛选参数在前端和后端各有一个解析函数，格式和规则完全一致（AC-3、AC-4、AC-10 和 ID 个数上限）；两边的单元测试共用同一份用例文件。导出功能（第 14 项）直接复用后端的解析函数和查询条件构造。

## Decision

**Chosen option**: Option 1：PostgreSQL 内置的二元切片数组索引

在 `message` 上新增 `search_text` 列（写入代码算好的小写搜索文字），用一个 `IMMUTABLE` SQL 函数把它切成相邻两个字的数组，建 GIN 表达式索引；每个词先用数组包含（`@>`）走索引缩小范围，再用 `LIKE` 精确复核。

**Implementation skills**: `postgresql-table-design` (`wshobson/agents`, `.claude/skills/postgresql-table-design/`) · `fastapi` (`fastapi/fastapi`, `.claude/skills/fastapi/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`) · `shadcn` (`shadcn-ui/ui`, `.claude/skills/shadcn/`) · `python-testing-patterns` (`wshobson/agents`, `.claude/skills/python-testing-patterns/`) · `vitest` (`antfu/skills`, `.claude/skills/vitest/`)

## Rationale

理由和备选方案：见 [rationale.md](rationale.md)。

## Feature design

### 数据模型

只新增，不改已有的列（spec 0002 的约定）。一次迁移。

**`message`**（已有，新增一列）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `search_text` | text | 否 | 小写的搜索文字，由 `build_search_text` 算好写入（AC-6、AC-20）。`NULL` 表示还没补建（AC-22）。ORM 里用 `deferred()`，现有的 `select(Message)` 不会把它读出来 |

**新函数** `harvester_bigrams(t text) RETURNS text[]`，`LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE`：返回 `t` 里所有相邻两个字符（按字符，不按字节）去重后的数组，长度小于 2 时返回空数组。

```sql
SELECT coalesce(array_agg(DISTINCT substr(t, i, 2)), '{}')
FROM generate_series(1, char_length(t) - 1) AS i
```

**新索引**
- `ix_message_search_bigrams`：`USING gin (harvester_bigrams(search_text))`。查询里必须写成同一个表达式才能用上。
- `ix_message_search_pending`：`(id DESC) WHERE search_text IS NULL`，部分索引，让补建取批次和数剩余条数都很快；补完后它几乎是空的。

**不变的表**：`discord_user`（作者联想直接查，行数少）、`channel`（线程展开用 `parent_id`）、`attachment`（文件名在写 `search_text` 时读）、`guild`。

**关系**：没有新表和新外键。

**迁移顺序**：建函数 → 加列 → 建两个索引（现在数据量小，普通 `CREATE INDEX` 就行；以后数据量大时重建要用 `CONCURRENTLY`，写进迁移的注释）。`downgrade` 反向删除。

### 搜索文字的生成（纯函数）

`harvester.db.search_text.build_search_text(content, forwarded, embeds, attachment_filenames) -> str`：

1. 收集各部分：`content`；每个 embed 的 `title`、`description`、每个 field 的 `name` 和 `value`、`author.name`、`footer_text`；`forwarded` 的 `content`、它的 embeds（同上）和 `attachment_names`；`attachment_filenames`（这条消息 `attachment` 表里所有行的 `filename`，按附件 ID 排序，包括 `removed_at` 有值的）。空的部分跳过。
2. 每部分按 AC-6 处理 Discord 标记（正则替换），再 `str.lower()`，再把连续空白（含换行）合并成一个空格并去掉首尾空白。
3. 各部分用 `\n` 连接。

`harvester.db.search.refresh_search_text(session, message_id)`：读这条消息的 `content`、`forwarded`、`embeds` 和全部附件文件名，调 `build_search_text`，`UPDATE ... SET search_text = :t WHERE id = :id AND search_text IS DISTINCT FROM :t`。`ingest_message` 里的调用点（都在 `_sync_attachments` 之后，同一个事务，AC-20）：
- 插入分支：在 `sync_media_for_message` 之后、`return "inserted"` 之前（这条路径提前返回，不经过末尾的 `flush`，必须单独接上）。
- 更新分支末尾：在 `_sync_attachments` 之后、`session.flush()` 之前，只有 `NewVersion`、`RefreshEmbeds` 或 `snap.attachments_known` 为真时才调用。
- 不调用：返回 `deleted`、`stale`、`skipped_type`、`missing_channel` 的路径；`BumpEditedAt` 和纯 `NoWrite` 且没有附件信息的情况。

补建的写法：`UPDATE ... SET search_text = :t WHERE id = :id AND search_text IS NULL`，不会覆盖 ingest 刚写好的值。

### 关键词解析（纯函数，前后端各一份）

后端 `harvester.api.search_query.parse_query(q: str) -> ParsedQuery | QueryError`，前端 `web/src/components/search/parse-query.ts` 的 `parseQuery(q): ParsedQuery | QueryError`（判别联合）。

- `ParsedQuery`：`include: tuple[str, ...]`、`exclude: tuple[str, ...]`，每个词已经小写、合并空白。
- `QueryError.reason`：`too_long`（超过 200 字符）、`too_many_terms`（超过 10 个）、`term_too_short`（某个词少于 2 个字符，附带这个词）。`exclude_only` 不在 `parse_query` 里判断（它要看筛选），由 `parse_filters` 判断。
- 规则按 AC-3、AC-4。大小写转换：后端 `str.lower()`，前端 `toLowerCase()`；字符数：后端 `len()`，前端 `[...s].length`。
- 共用用例文件 `backend/tests/fixtures/search_query_cases.json`（输入和期望输出），pytest 和 Vitest 都读它（AC-26）。

### 查询构造

`harvester.db.search.search_conditions(filters: SearchFilters) -> list[ColumnElement[bool]]`，搜索、总数和以后的导出共用（AC-26）：

- 包含词：所有包含词的切片合并成一个条件 `harvester_bigrams(search_text) @> (harvester_bigrams(:t1) || harvester_bigrams(:t2) ...)`，每个词再各加一个 `search_text LIKE '%' || :t_escaped || '%' ESCAPE '\'` 复核。
- 每个排除词：`search_text NOT LIKE ...`（还没补建的行因为 `NULL` 自然被排除，符合 AC-22）。
- 服务器和频道：`guild_id = ANY(:guilds) OR channel_id = ANY(:expanded_channels)`，`expanded_channels` = 选中的频道 ID 加上 `channel` 里 `parent_id = ANY(:channels) AND type = ANY(THREAD_CHANNEL_TYPES)` 的 ID（查询里用子查询，不在应用层展开）。注意 `parent_id` 对普通频道是分类、对线程才是父频道，所以必须带 `type` 条件。只选了服务器或只选了频道时只用一边。
- 作者：`author_id = ANY(:authors)`。
- 时间：用消息 ID 比较（雪花 ID 就是创建时间，能走主键），`id >= snowflake_floor(from)`、`id < snowflake_floor(to)`。新增纯函数 `snowflake_floor(dt) -> int`，和已有的 `snowflake_time` 互为反函数。
- 翻页：`id < :before`，`ORDER BY id DESC LIMIT 51`（多取一条判断还有没有下一页）。

**固定计划**（AC-21）：含包含词的搜索和总数，在自己的事务里先执行：

```sql
SET LOCAL plan_cache_mode = force_custom_plan;  -- asyncpg 预备语句也按实际参数规划
SET LOCAL enable_indexscan = off;               -- 不许按主键倒序扫描再逐行过滤
SET LOCAL enable_seqscan = off;                 -- 不许全表扫描
```

这样只剩位图扫描：GIN 的结果和频道、作者、服务器的 btree 索引以及 ID 范围取交集（BitmapAnd），回表复核后取前 51 条排序。罕见词一定快；加了筛选的常见词也快，因为复核只发生在交集上；最坏情况是不加筛选的常见词，由超时兜底（见 Consequences）。只有筛选或只有排除词的搜索不设这两个开关，交给计划器（这时按主键倒序取前 51 条通常最快）。理由：二元切片的统计只记录最常见的 1000 个元素，几个切片之间又高度相关，靠估算选计划不可靠。

**超时**：搜索和总数各在自己的事务里 `SET LOCAL statement_timeout`，搜索 10 秒、总数 5 秒（AC-24）。识别方式：`isinstance(e, DBAPIError) and getattr(e.orig, "sqlstate", None) == "57014"`（`e.orig` 是 SQLAlchemy 包装过的 asyncpg 异常，不是 `QueryCanceledError` 本身；它会被映射成普通的 `DBAPIError`，不处理就会变成 500）。搜索转成项目异常 `SearchTimeout`（503 `search_timeout`），总数返回 `count: null`。

**总数**：`SELECT count(*) FROM (SELECT 1 FROM message WHERE ... LIMIT 1001)`，结果大于 1000 时返回 `count: 1000, capped: true`。

### 网址和接口参数

网址（`/search`）和接口用同一组参数名。ID 都是十进制字符串，多个用逗号分隔。

| 参数 | 网址 | 接口 | 说明 |
|---|---|---|---|
| `q` | 有 | 有 | 关键词原文，最多 200 字符 |
| `guild` | 有 | 有 | 服务器 ID，最多 50 个 |
| `channel` | 有 | 有 | 频道或线程 ID，最多 200 个 |
| `author` | 有 | 有 | 用户 ID，最多 20 个 |
| `range` | 有 | 无 | `today`、`7d`、`30d`；和 `from`/`to` 互斥 |
| `from`、`to` | 有 | 有 | ISO 8601 带时区（前端写 UTC，`Z` 结尾）；`from` 包含、`to` 不包含 |
| `before` | 无 | 有 | 翻页游标，消息 ID |
| `limit` | 无 | 有 | 1 到 50，默认 50 |

`range` 由浏览器换算成 `from` 再发给接口（`today` = 本地零点，`7d` = 现在减 7×24 小时，`30d` 同理），换算在每次第一页请求（包括「刷新」）时做一次（AC-10、AC-15）。

`from` 早于 Discord 纪元时按纪元算；`to` 不填就是没有上限；网址里 `from`/`to` 和 `range` 同时出现时以 `from`/`to` 为准（AC-10）。ID 参数里重复的 ID 去重，不报错。

后端 `harvester.api.search_query.parse_filters(...) -> SearchFilters | FilterError`。路由把所有参数按原始字符串（`str | None`）收进来交给它手动解析，不用现有的 `QueryId`、`QueryIds` 或 pydantic 校验，否则出错时返回的是 `validation_error` 而不是 `invalid_filters`；前端 `web/src/components/search/search-params.ts` 的 `parseSearchParams(URLSearchParams) -> SearchFilters`（不合法的值丢掉，不报错）和 `toSearchParams(filters) -> URLSearchParams`，以及 `resolveRange(filters, now) -> {from, to}`。

### API surface

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/search/messages` | GET | `q`、`guild`、`channel`、`author`、`from`、`to`（都可选，至少一个）、`before`、`limit` | `items[]`：`message`（现有 `MessageOut`）、`guild {id, name}`、`channel {id, name, type, status, collect_enabled, parent {id, name} \| null}`；顶层 `users[]`、`channels[]`（提及解析用，和现有 `MessagesOut` 同样的形状和批量加载）；`next_before`（字符串或 `null`） | session | 422 `invalid_query`、422 `invalid_filters`（没有任何条件、ID 格式或个数不对、`from` 不早于 `to`）、503 `search_timeout`、401 |
| `/api/search/count` | GET | 同上（不含 `before`、`limit`） | `count`（整数或 `null`）、`capped`（布尔） | session | 422 同上、401 |
| `/api/search/status` | GET | 无 | `index_pending`（`search_text IS NULL` 的消息数，走 `ix_message_search_pending`，不受总数超时影响） | session | 401 |
| `/api/search/channels` | GET | 无 | `guilds[]`：`id`、`name`、`status`、`channels[]`（`id`、`name`、`type`、`parent_id`、`category_name`、`status`、`collect_enabled`），已按 AC-8 排好序 | session | 401 |
| `/api/search/authors` | GET | `q`（1 到 50 字符）或 `ids`（最多 20 个），二选一 | `authors[]`：`id`、`username`、`global_name`、`avatar_hash`（前端用现有的 `avatarUrl(id, hash)` 拼地址） | session | 422 `invalid_filters`（两个都给或都不给）、401 |
| `/api/discord/channels/{id}/messages` | GET | 新增第五种互斥取法 `around=<消息ID>`，固定前 25、目标、后 25，不受 `limit` 影响 | 现有格式，按 ID 从旧到新；`has_more` 表示更早还有，新增 `has_newer` 表示更新还有（其他取法里 `has_newer` 为 `null`）。前后各多取一条来判断 | session | 404 `message_not_found`（目标不在这个频道）、404 `channel_not_found`、422 多种取法同时给 |

`/api/search/messages` 的 `items` 和顶层 `users`、`channels` 复用频道消息接口现有的批量加载（附件、媒体、回复摘要、提及的用户和频道）。`channel.parent` 只对线程（`type` 属于线程类型）给出，普通频道为 `null`。

`/api/search/channels`：频道有消息的判断用 `EXISTS (SELECT 1 FROM message WHERE channel_id = c.id)`（走 `ix_message_channel_id_id`）；一个父频道或论坛只要它自己或它的任一线程有消息就列出。分类名和分类 `position` 从 `type = 4` 的分类行读。`type` 是 Discord 的整数频道类型（和 `ChannelDetailOut` 一致），`status` 沿用 spec 0006 的取值。

`/api/search/authors?q=`：`EXISTS` 至少一条消息（走 `ix_message_author_id_id`），`lower(username) LIKE` 或 `lower(global_name) LIKE` 子串（`%`、`_`、`\` 先转义），按 `username` 排序，最多 20 个。`?ids=` 只返回库里存在的。

### 错误格式

沿用现有的 `{"error": {code, message, fields?: [{loc, type}]}}`。给 `ApiError` 加可选的 `fields` 参数，前端 `toUiError` 同步读出来：

| 情况 | `code` | `fields` |
|---|---|---|
| 关键词超长、词太多、词太短、只有排除词 | `invalid_query` | `[{loc: ["query", "q"], type: "too_long" \| "too_many_terms" \| "term_too_short" \| "exclude_only"}]` |
| ID 不是十进制、超出 int64、个数超限 | `invalid_filters` | `[{loc: ["query", "<参数名>"], type: "invalid_id" \| "too_many_ids"}]` |
| `from`、`to` 格式不对或开始不早于结束 | `invalid_filters` | `[{loc: ["query", "from"], type: "invalid_datetime" \| "empty_range"}]` |
| 没有任何条件 | `invalid_filters` | `[{loc: ["query"], type: "no_conditions"}]` |

### 前端

- **页面** `web/src/app/(dashboard)/search/page.tsx`，依赖 `useSearchParams` 的部分包在 `Suspense` 里，fallback 是同样结构的骨架（spec 0003 的静态导出要求）。组件放 `web/src/components/search/`。
- **图标栏**：`server-rail.tsx` 加「搜索」图标按钮（lucide `Search`），`aria-label="搜索"`，当前在 `/search` 时高亮；`routes.ts` 加 `searchHref(filters?)`。
- **筛选栏**：关键词输入框（回车或「搜索」按钮提交；提交前用 `parseQuery` 校验，错误显示在输入框下方，`aria-describedby` 关联）；「服务器和频道」弹出选择器（数据来自 `/api/search/channels`，按 AC-8）；作者联想框（输入 300 毫秒防抖后请求 `/api/search/authors?q=`，选中的人显示成可移除的标签，刷新时用 `ids=` 还原名字）；时间下拉（快捷项和自定义）。筛选变化立即生效（`router.replace` 更新网址），关键词只在提交时生效。
- **结果**：`useInfiniteQuery`，`queryKey` 是规范化后的筛选（含换算后的 `from`/`to`），`staleTime: Infinity`、不在窗口聚焦时重新取（快照，AC-15）；「刷新」重新换算时间并重置查询。关键词输入框的草稿只是本地状态，提交时才写进网址；`queryKey` 只从网址解析出来的筛选生成，所以筛选连续变化时 TanStack Query 只保留最新的那次查询。总数用独立的 `useQuery`，补建进度用另一个 `useQuery`（`refetchInterval` 30 秒，`index_pending` 为 0 时停）。结果列表用 TanStack Virtual，行高先估 120px，用 `measureElement` 动态测量，滚到接近底部时取下一页；下一页失败时在列表底部显示错误和「重试」（AC-16）。
- **结果卡片** `search-hit.tsx`：头部（服务器 › 频道或「父频道 › 线程」、状态标记、「跳到上下文」链接），下面是现有的 `MessageRow`，传 `groupStart: true`、从响应顶层 `users`、`channels` 建的 `resolver`（和频道页一样）、`highlightTerms` 和 `onAuthorClick`。
- **高亮** `highlight.ts`：纯函数 `splitHighlights(text, terms) -> readonly {text, hit}[]`。用每个词构造不分大小写的 Unicode 正则（`iu` 标志，词里的特殊字符先转义，短语里的空格换成 `\s+`）直接在原文上找，不能先转小写再用下标切回原文（转小写可能改变字符数）。多个词重叠时合并。`highlightTerms` 一路传进 `lib/discord-markdown/renderers.tsx` 的文字节点、`Embed`（标题、描述、字段值）、`ForwardedBlock` 和附件名（AC-13）。没有 `highlightTerms` 时行为和现在完全一样。
- **频道页**：`channelHref(id, messageId?)` 生成 `/channels?id=&m=`。`use-channel-messages.ts` 新增「以某条为中心」模式（AC-17 到 AC-19）：
  - 用独立的 `queryKey`（带中心消息 ID），第一页用 `around` 取，同时记录 `has_more`（更早）和 `has_newer`（更新）；向旧翻沿用现有逻辑，向新翻用 `getPreviousPageParam` 和 `after`。
  - `has_newer` 为真时：关掉 SSE 的 `fetchNew`（包括 `hasGap` 时整页换成最新并让 `epoch` 加一的逻辑）和 30 秒的 `reconcile`；保留按 `ids=` 刷新已加载消息；补采完成的 `refreshLoaded` 改成按当前中心重新取 `around`。
  - 某次 `after` 返回 `has_more` 为假后，切换回普通模式（现有的 `queryKey` 和全部逻辑），保留滚动位置。
  - `use-message-scroll.ts` 新增 `scrollToMessage(id)`（`scrollToIndex` 居中）和临时高亮状态；`has_newer` 为真时关闭贴底，不上报已读。
- **补建横幅**：`index_pending > 0` 时显示（AC-23）。

### 补建任务

`harvester.api.search_backfill.run_search_backfill(factory, sleep)`，在 api 的 `lifespan` 里用 `asyncio` 任务启动，关闭时取消（api 只有一个 worker，所以只有一个补建循环）：

1. 一个事务：`SELECT id FROM message WHERE search_text IS NULL ORDER BY id DESC LIMIT 1000 FOR UPDATE SKIP LOCKED`，再批量读这些行的内容和附件文件名，逐条 `build_search_text`，批量 `UPDATE ... WHERE id = :id AND search_text IS NULL`，提交。已删除的消息也照样补建。
2. 本批有行：停 0.2 秒，继续。本批为空：如果这一轮补过至少一批，执行一次 `ANALYZE message`（让计划器拿到新的统计），然后停 10 分钟再查（AC-22）。
3. 出错：记日志（`error_type`、本批条数，不记内容），停 30 秒重试。
4. 每 50 批记一次进度日志（已处理条数），一轮补完时记一条完成日志。
5. 本地开发用 `--reload` 时，旧进程退出前可能和新进程同时跑一小会儿；因为取批用 `SKIP LOCKED`、写入只写 `NULL` 的行，两份循环同时跑也不会出错。

### Value sourcing

| Action | Value produced / displayed | Source |
|---|---|---|
| 搜索 | 命中的消息 | `message.search_text` 加筛选条件，`search_conditions` 构造 |
| 搜索 | 每个词的二元切片 | `harvester_bigrams(:term)`，数据库里算，和索引同一个函数 |
| 搜索 | 时间范围的 ID 边界 | `from`/`to` 参数经 `snowflake_floor` 换算 |
| 搜索 | 「最近 7 天」「今天」对应的时间 | 浏览器当前时间和本地时区，`resolveRange` 在第一页请求时换算 |
| 搜索 | 线程展开后的频道集合 | `channel.parent_id` 子查询 |
| 搜索 | 结果头部的服务器名、频道名、父频道名、频道状态 | `guild.name`、`channel.name`、父 `channel.name`（只对线程类型）、`channel.status`、`channel.collect_enabled`，查询时连表 |
| 搜索 | 消息卡片内容、作者、头像、附件、回复摘要 | 现有 `MessageOut`（含 `author_avatar_hash`）和批量加载（spec 0008、0011） |
| 搜索 | 正文里提及的用户名和频道名 | 响应顶层 `users`、`channels`，现有的提及批量加载 |
| 搜索 | 计划设置 | 是否有包含词（`ParsedQuery.include` 非空） |
| 搜索 | 下一页游标 | 本页最后一条的 `id`（多取的第 51 条存在时） |
| 搜索 | 高亮的词 | 前端 `parseQuery(q).include` |
| 总数 | 命中数和是否封顶 | `count(*)` 加 `LIMIT 1001` |
| 补建进度 | 还没补建的条数 | `GET /api/search/status`：`count(*) WHERE search_text IS NULL`，走 `ix_message_search_pending` |
| 频道选择器 | 服务器和频道列表、标记、排序 | `guild`、`channel` 表加 `EXISTS message`；分类名和 `position` 来自 `type = 4` 的分类行 |
| 作者联想 | 候选人、名字、头像 | `discord_user.username`、`global_name`、`avatar_hash`，加 `EXISTS message` |
| 跳到上下文 | 目标频道 | 结果里的 `message.channel_id`（线程就是线程自己的 ID） |
| 跳到上下文 | 前后 25 条和两个方向是否还有 | `around` 取法，`ix_message_channel_id_id`，前后各多取一条得出 `has_more`、`has_newer` |
| 跳到上下文 | 是否已到最新 | 第一次的 `has_newer`，之后每次 `after` 的 `has_more` |
| 写入 | `search_text` | `build_search_text`，输入是 `message` 的 `content`、`forwarded`、`embeds` 和 `attachment.filename` |
| 错误提示 | 具体原因文字 | `QueryError.reason`（前端本地）或接口错误 `fields[].type`，经 `toUiError` |

### Key invariants

- `search_text` 只由 `build_search_text` 生成；不是 `NULL` 时，它总是等于用这条消息当前的内容和附件算出来的结果（每条写入路径都在同一事务里重算）。
- 查询里的索引表达式和建索引的表达式完全一样：`harvester_bigrams(search_text)`。
- 含包含词的搜索和总数，总是在设置了 `force_custom_plan`、`enable_indexscan = off`、`enable_seqscan = off` 的事务里执行；这些设置只用 `SET LOCAL`，不影响同一连接上的其他查询。
- 线程展开总是带 `type` 属于线程类型的条件，不会把分类下的普通频道展开进来。
- `harvester_bigrams` 是 `IMMUTABLE`，以后修改它的定义必须在同一个迁移里重建 `ix_message_search_bigrams`。修改 `build_search_text` 的规则必须把 `search_text` 置为 `NULL`，让补建重新生成。
- 每个包含词至少 2 个字符，所以它的二元切片永远不为空，含包含词的查询总能用上索引。只有排除词的查询不走关键词索引，所以必须带其他筛选。
- 前后端的解析规则一致，由共用用例文件保证。
- 搜索接口只读，不写任何表；补建只写 `search_text` 一列。

### Security model

单用户 dashboard，所有新接口都走现有的 `require_session` 和统一错误格式（spec 0004）。关键词只作为绑定参数进 SQL（`LIKE` 的通配符先转义），不拼接字符串。日志不记关键词和消息内容（AC-25）。没有新的密钥或敏感数据。

### Configuration required

不需要新的环境变量。超时（10 秒、5 秒）、批大小（1000）、补建间隔（0.2 秒、10 分钟、30 秒）都是代码里的常量。

### Critical test scenarios

- Happy path：库里有中英混合消息，搜「空投 btc」只返回同时包含两者的消息，最新在前，翻页不重复不遗漏；从结果跳到频道页，目标居中高亮，上下都能继续滚，verifies **AC-3**、**AC-5**、**AC-12**、**AC-17**
- 文字覆盖：只出现在 embed 描述、转发正文、已移除附件文件名里的词都能搜到；搜提及里的用户 ID 数字搜不到；搜 `pepe` 命中自定义表情，verifies **AC-6**
- 规则：排除词、引号短语、`-"短语"`、`--foo`、`a"b`、全角引号、未闭合引号、`50%` 里的 `%` 当普通字符、单个字的词和 `-a` 被拒绝、只有排除词而没有筛选被拒绝（前后端用例一致），verifies **AC-3**、**AC-4**、**AC-5**、**AC-26**
- 筛选：选父频道包含线程，但选一个频道不会把同一分类下的其他频道带进来；服务器和频道取并集；作者多选；时间边界（开始包含、结束不包含）；已删除频道和已删除消息出现在结果里；错误参数返回带 `fields` 的 `invalid_filters`，verifies **AC-7** 到 **AC-11**
- 写入：插入路径和更新路径都写好 `search_text`；编辑消息后旧词搜不到、新词搜得到；附件同步后文件名可搜；删除事件和只改 `edited_at` 不改 `search_text`；现有 `select(Message)` 的 SQL 里没有 `search_text`，verifies **AC-20**
- 索引：在生产相同的 `SET LOCAL` 下，两个汉字和三个字母的词的 `EXPLAIN`（绑定参数）用到 `ix_message_search_bigrams`，没有 `message` 的 `Seq Scan` 和主键 `Index Scan`，verifies **AC-21**
- 结果卡片：提及显示成名字；高亮在大小写不同、短语含多个空格时都正确，`**B**TC` 不高亮也不报错，verifies **AC-12**、**AC-13**
- 补建：先插入 `search_text` 为空的行，补建跑完后全部有值；补建和 `ingest_message` 并发写同一条时结果正确；补建期间横幅显示剩余条数，verifies **AC-22**、**AC-23**
- Failure case：把超时调到很小，搜索返回 503 `search_timeout`（不是 500）并显示提示；总数超时返回 `count: null`，结果照常；第二页失败时已加载的结果保留，verifies **AC-14**、**AC-16**、**AC-24**
- 跳转失败：`m` 指向别的频道的消息时显示提示并加载最新一页；以某条为中心时实时新消息不追加、不跳回底部、不标记已读；向下翻到 `has_more` 为假后恢复贴底和追加，verifies **AC-18**、**AC-19**
- Auth/permission：未登录调用五个新接口和 `around` 都返回 401，verifies **AC-25**

## Build plan

按 Tracer Bullet：第一步先打通「输入一个词 → 走索引 → 页面上看到结果」的整条细线，之后逐段加粗。

1. **打通细线**：迁移（`harvester_bigrams`、`search_text`、两个索引），ORM 里 `search_text` 设 `deferred`；`build_search_text` 和 `refresh_search_text`，接进 `ingest_message` 的插入分支和更新分支；最简 `parse_query`（只有空白分词和 2 字符下限）；`search_conditions` 只处理包含词，带固定计划的 `SET LOCAL`；`GET /api/search/messages`（只有 `q`、`before`、`limit`，含顶层 `users`、`channels`）；`/search` 页面（输入框和独立的结果卡片，无筛选）和图标栏入口；OpenAPI 同步，satisfies **AC-1**、**AC-5**、**AC-6**、**AC-12**、**AC-20**、**AC-21**
2. **筛选和完整语法**：`ApiError` 加 `fields`、`toUiError` 同步；`parse_query` 补全引号、排除、限制；`parse_filters`（原始字符串手动解析、`exclude_only`）、`snowflake_floor`、带 `type` 条件的线程展开、服务器和频道并集；`/api/search/channels`（排序和分类名）、`/api/search/authors`；前端 `parseQuery`、`search-params.ts`、`resolveRange`、筛选栏（选择器、作者联想、时间）、网址同步、空条件提示；共用用例文件，satisfies **AC-2**、**AC-3**、**AC-4**、**AC-7** 到 **AC-11**、**AC-26**
3. **跳到上下文**：`around` 取法、`has_newer` 和 `message_not_found`；`channelHref` 加消息参数；频道页「以某条为中心」模式（独立 `queryKey`、双向翻页、关掉 `fetchNew` 和 `reconcile`、按中心重取、切回普通模式）、`scrollToMessage`、高亮、「回到最新」、找不到时的提示，satisfies **AC-17**、**AC-18**、**AC-19**
4. **补建、总数和收口体验**：`run_search_backfill` 接进 `lifespan`（含补完后的 `ANALYZE`）；`/api/search/count`（封顶）和 `/api/search/status`；超时识别（SQLSTATE `57014`）和 `SearchTimeout`；补建横幅和 30 秒刷新、总数显示、「刷新」和快照行为、`splitHighlights` 和一路传进渲染器的高亮、各状态（含下一页失败）和窄屏筛选面板、日志字段，satisfies **AC-13** 到 **AC-16**、**AC-22** 到 **AC-25**
5. **测试和检查**：后端集成测试（连真实 PostgreSQL，含 `EXPLAIN` 断言和并发补建）、前端 Vitest（解析、网址往返、高亮、以某条为中心的状态）；更新 spec 0002 和 spec 0001 里「搜索由第 13 项决定」的几处；ruff、pyright、Biome、typecheck、build 全部通过，satisfies **AC-1** 到 **AC-26**

## Consequences

**Positive**:
- 两个字的中文词和短英文词都能走索引，不依赖分词词典，新词、币名、缩写不会漏。
- 不装扩展、不换数据库镜像，CI、生产、备份恢复的流程都不变。
- 筛选的格式和查询构造只有一份，导出直接复用。
- 已关闭和已删除频道的消息第一次有了入口（搜索加跳转），部分覆盖 Deferred 里的「查看已关闭频道的消息」。
- 频道页的「以某条为中心」以后可以给回复跳转和关键词提醒复用。

**Negative / tradeoffs**:
- 索引比较大：每条消息的去重二元切片大约是文字长度那么多条目。几百万条消息时，索引可能有几个 GB（`/check verify` 时用生成的数据量一下，写进 verify 记录）。Lightsail 磁盘要留出这部分空间（`pg_dump` 不导出索引内容，只导出建索引的语句）。
- 很常见的两个字（比如「的是」）命中太多、又没加筛选时，固定的位图计划要把所有命中行取回再排序，可能碰到 10 秒超时；这时只能靠加筛选，提示里会这样告诉你。这是为了让罕见词和加了筛选的查询总是稳定地快而接受的代价。
- 补建时每行的 `UPDATE` 涉及两个索引里的列，不能原地更新（HOT），几百万行补完后 `message` 表会膨胀到接近两倍，还会产生大量 WAL 和 GIN 写入。要留够磁盘，补完后跑一次 `VACUUM`；`/check verify` 时确认 autovacuum 跟得上。
- 恢复备份时要重建 GIN 索引，几百万行要几分钟，内存受 `maintenance_work_mem` 影响；恢复演练（spec 0009）会变慢。`pg_dump` 里多出一份 `search_text`，备份体积接近正文的两倍。
- 每次写消息多一次读附件文件名和一次 `UPDATE`，GIN 索引也会让写入变慢一些；对每秒最多几十条的实时采集影响可以忽略，大批量历史采集会稍慢。
- `search_text` 是一份存下来的派生数据，规则一变就要整体重新补建。
- 只搜当前内容，被编辑掉的旧文字搜不到。
- 不做全半角和简繁转换，搜「ＢＴＣ」搜不到「BTC」。

**Neutral**:
- 解析规则在 Python 和 TypeScript 里各写一份，靠共用用例文件保持一致。
- api 进程第一次开始写 `message` 表（只写 `search_text`），collector 仍然负责消息本身；进程之间仍然只通过数据库协作。
- 以后数据到千万级、或者需要分词和相关度时，可以换成 PGroonga 之类的扩展，`search_conditions` 是唯一要改的地方。

## Follow-up

- [x] 写完后更新 spec 0002 的「以后的表」里「搜索索引（第 13 项决定）」和末尾的后续迁移列表，指向本 spec；更新 spec 0001 技术栈表里的「搜索」一行（构建步骤 5 里做）。
- [ ] `/check verify` 时用一个临时脚本（放临时目录，不进仓库）生成几十万到几百万条中英混合消息，记录索引大小、补建前后的表大小和几个常见词、罕见词的查询耗时，确认「用着不卡」。
- [ ] 以后的导出（第 14 项）复用 `search_conditions` 时，服务器和频道的 `OR` 条件在大范围流式扫描下的计划要一起验证。
- [ ] 回来续 `/architect 导出`（第 14 项），筛选格式按本 spec 的「网址和接口参数」和 `search_conditions` 复用。
- [ ] Deferred 里的「查看已关闭频道的消息」：搜索页已经能找到这些消息，是否还需要管理页入口，到时候再定。
- [ ] 作者联想只匹配 `username` 和 `global_name`，不匹配服务器昵称（`author_display_name` 是每条消息上的值）；如果平时更常记得昵称，再考虑给作者加一个最近昵称列。
