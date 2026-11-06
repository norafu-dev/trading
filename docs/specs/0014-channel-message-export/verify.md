# Verify: 频道消息导出 · spec 0014 · updated 2026-11-06
_Steps derived from spec 0014 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## UI / manual
- [ ] 打开文字频道、公告频道、线程 → 标题栏下方有「导出」按钮；打开论坛、媒体、语音、分类 → 没有；已关闭采集或已删除的频道照常有 → AC-1
- [ ] 在线程页导出 → 文件只含线程自己的消息；在父频道导出 → 不含线程里的消息 → AC-1
- [ ] 点「导出」→ 底部出现操作栏，消息列表被推高、最后一条不被盖住 → AC-2
- [ ] 选择模式下点图片、链接、附件、视频、音频所在的位置 → 只是选中，不放大图片、不开链接、不播放 → AC-2
- [ ] 先点新的再点旧的 → 起点终点自动按时间排好；同一条点两次 → 只导出这一条；再点第三条 → 以它为新的第一端重新开始 → AC-2
- [ ] 点「从最早」「到最新」：只替换对应的一端；再点亮着的那个取消；只有一端时提示文字正确，下载按钮不可用 → AC-3
- [ ] 范围内消息有背景高亮，起点和终点有「起点」「终点」标记，标记出现和消失时行高不变；操作栏显示两端的本地时间和作者名，开放的一端显示「最早」「最新」 → AC-4
- [ ] 点「取消」、按 Esc、切换频道 → 选择清空；点「跳到最新」→ 选择保留；下载后仍停在选择模式，网址里没有选择 → AC-5
- [ ] 范围确定 → 先显示「正在统计」再显示「共 N 条」（含已删除）；快速改范围 → 旧结果不会覆盖新结果 → AC-6
- [ ] 选超过 10 万条的范围（或临时把 `EXPORT_MAX_MESSAGES` 改小）→ 显示「超过 10 万条上限，请缩小范围」，下载不可用 → AC-7
- [ ] 选一个和采集缺口有交集的范围 → 显示「这段时间里有 K 段没有采集（共约 X）」和去 `/history` 的链接，下载仍可用；不重叠的范围不显示；已删除频道不显示 → AC-8
- [ ] 停掉 api 后改范围 → 操作栏显示错误和「重试」，恢复后点重试成功；两端都开放的空频道 → 「频道里没有消息」 → AC-9
- [ ] 选格式 CSV 和 JSONL 各下载一次 → 浏览器直接下载，页面不跳走；文件名形如 `服务器_频道_起始日期_结束日期.csv` → AC-10, AC-11
- [ ] 用名字含 `/ : * ?` 和空格的频道、很长的中文名导出 → 文件名里这些字符变成 `_`，名字最多 30 个字符 → AC-11
- [ ] 导出过程中往频道发新消息、编辑、删除 → 文件内容不变；开放终点时文件条数可以比预览多几条 → AC-12
- [ ] 正文含 `<@123>`、`<#123>`、`<t:123:R>`、`<:pepe:123>`、`</cmd:123>` 的消息 → 文件里去掉前三种，表情只留 `:pepe:`，命令变 `/cmd`，大小写和空白不变 → AC-13
- [ ] 用 Asia/Shanghai 和另一个时区的浏览器各导出同一段 → 时间带对应偏移、精确到秒；embed 的 `timestamp` 原样不变 → AC-14
- [ ] 用 Excel 双击打开 CSV → 中文不乱码；多行正文、含逗号和引号的单元格正确；以 `=`、`+`、`-`、`@` 开头的正文单元格前面有 `'`，ID 和时间列没有 → AC-15
- [ ] 打开 JSONL → 每行一个对象，中文不转义，ID 是字符串，带 `revisions`；没改过的消息 `revisions` 为空数组 → AC-16
- [ ] 导出 embed 里带图的消息（如「华尔街聚合」的 Trader Gauls 频道）→ JSONL 里该 embed 有 `image_media_url`（`https://media.norafu.com/<object_key>`，在没登录的隐私窗口里也能打开图片）且 `image_url` 不变；CSV 的 `embeds` 列末尾有 `图片: 链接`；`pending` 或没有副本的 embed 图没有这个字段；一条消息里有多个 embed 时链接和各自的图对得上；转发和 `revisions` 里的 embed 没有 → AC-17
- [ ] 导出带已保存附件、未保存附件、被移除附件、转发附件的消息 → 已保存的有 `https://media.norafu.com/<object_key>` 链接（不登录也能打开，是原文件不是缩略图），其他没有；CSV 不列被移除的，JSONL 列出并带 `removed_at`；整个文件里搜不到 `/api/media/` → AC-17
- [ ] 把一份线上导出的 JSONL 交给一个外部 AI（不带任何登录信息），让它描述其中两张 embed 图 → 它能读到图的内容 → AC-17
- [ ] 本地 `MEDIA_STORAGE=local` 导出一份 → 链接是 `media-dev` 下的绝对路径，`ls` 这个路径文件存在；交给本机 agent 能读到图 → AC-17
- [ ] 公开域名的安全头：`curl -I https://media.norafu.com/<某个已存的 object_key>` → 200，带 `Content-Security-Policy: sandbox` 和 `X-Content-Type-Options: nosniff`；`curl -I https://media.norafu.com/` → 不列出文件 → AC-17
- [ ] 已登录状态下导出 → 正常；登出后直接打开下载网址 → 401 → AC-21

## Commands
- [ ] `cd backend && uv run pytest tests/api/test_export.py tests/api/test_export_format.py` → 全部通过（覆盖范围、快照、上限、附件、断开、中途失败、日志） → AC-6 到 AC-21
- [ ] `cd backend && uv run pytest tests/db/test_search_text.py` → 全部通过，搜索文字不变 → AC-22
- [ ] `cd web && pnpm exec vitest run src/components/export src/components/messages` → 全部通过（选择转换表、操作栏、覆盖按钮） → AC-2 到 AC-10
- [ ] `curl -i -b <会话 cookie> 'http://localhost:8080/api/channels/<id>/export?format=csv&tz=../x'` → 422 `invalid_export`，`fields` 指出 `tz` → AC-18
- [ ] `curl -i -b <会话 cookie> 'http://localhost:8080/api/channels/<id>/export?format=csv&tz=UTC&start=<别的频道的消息ID>'` → 404 `message_not_found` → AC-18
- [ ] `curl -N -b <会话 cookie> '.../export?format=csv&tz=UTC' --max-time 1` 提前断开，然后在数据库里 `select state from pg_stat_activity` → 没有残留的 `idle in transaction`，日志有 `export_aborted` → AC-19
- [ ] 查看 api 日志 → 每次导出一行 `export_finished`，只有频道 ID、格式、条数、字节数、耗时，没有消息内容 → AC-21

## Value sourcing（spec 的来源表）
- [ ] 选择模式的两端时间和作者名 → 来自已加载的消息，对照消息行上显示的值 → AC-4
- [ ] 预览条数 N → 和 `select count(*) from message where channel_id=… and id between …` 一致 → AC-6
- [ ] 开放一端的实际消息 → 预览返回的 `range_start`、`range_end` 等于频道最早、最新一条的 ID → AC-3
- [ ] 缺口段数和总时长 → 和 `/history` 页同一频道的缺口对照，只保留与范围有交集的部分；开放终点算到现在 → AC-8
- [ ] 转发附件的类型和大小 → 对照 `media.declared_content_type`、`media.declared_size_bytes` → AC-17
- [ ] 上限 100000 → 用缩小后的常量验证预览 `over_limit` 与下载 422 `export_too_large` → AC-7
- [ ] 时区 → 改浏览器时区后文件里的偏移和文件名里的日期随之变化（例如 UTC 20:00 在上海算次日） → AC-11, AC-14
- [ ] 文件名里的服务器名、频道名 → 对照 `guild.name`、`channel.name`；线程用线程名 → AC-11
- [ ] 附件链接开头 → r2 时等于配置的 `MEDIA_PUBLIC_BASE_URL`，local 时等于 `MEDIA_LOCAL_DIR` 的绝对路径 → AC-17
- [ ] 附件链接的路径部分 → 等于 `select sf.object_key from media m join stored_file sf on sf.id = m.file_id where m.id = …` → AC-17
- [ ] embed 图片副本链接 → `image_media_url`、`thumbnail_media_url` 的路径部分等于 `media` 里 `message_id=… and kind in ('embed_image','embed_thumbnail') and slot=… and status='stored'` 那一行经 `file_id` 关联的 `stored_file.object_key` → AC-17
- [ ] 删除和编辑标记 → 对照 `deleted_at`、`edited_at`、`version`；旧版本对照 `message_revision` → AC-12, AC-16

## Acceptance-criteria coverage
- AC-1 … 前三条 UI 步骤 · AC-2 … 选择模式相关步骤和 Vitest · AC-3 … 快捷按钮步骤 · AC-4 … 标记步骤 · AC-5 … 取消和保留步骤
- AC-6 … 条数步骤 · AC-7 … 上限步骤 · AC-8 … 缺口步骤 · AC-9 … 错误和空频道步骤
- AC-10 … 下载步骤 · AC-11 … 文件名步骤 · AC-12 … 快照步骤 · AC-13 … 标记处理步骤 · AC-14 … 时区步骤
- AC-15 … Excel 步骤 · AC-16 … JSONL 步骤 · AC-17 … 附件步骤 · AC-18 … curl 校验步骤 · AC-19 … 提前断开步骤
- AC-20 … 事务设置由 `test_snapshot_transaction_settings` 覆盖，超时由 `test_count_timeout` 覆盖 · AC-21 … 登录和日志步骤 · AC-22 … 搜索文字测试
