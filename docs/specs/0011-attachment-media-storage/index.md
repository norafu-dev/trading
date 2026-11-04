# 0011. 附件与图片保存：R2 存储、collector 下载队列和签名链接

**Date**: 2026-11-04
**Status**: In Progress

## Summary

Discord 的附件链接带过期签名，过一段时间就打不开。这个 spec 让 collector 把消息附件、转发里的附件，以及 embed 里由 Discord 托管的图片，下载到一个新的私有 R2 桶（Cloudflare 的按文件存储服务），按「频道 ID / 年月」分目录存放，同一频道同一个月里内容相同的文件只存一份，图片顺便生成小尺寸缩略图。dashboard 通过 api 拿到一个 1 小时有效的签名链接（带签名、到期作废的临时地址）去读文件，不再依赖 Discord 原链接。单个文件上限和总容量在设置页里改，空间满了就停止下载并提醒；已经入库的旧附件会先向 Discord 换新链接再补下载。

## Rationale

Reasoning and options: see [rationale.md](rationale.md).

## Requirements

**范围**：
- 包含：消息自带的附件；转发消息里的附件（只限这个功能上线后入库的）；embed 大图和缩略图中由 Discord 托管的那部分；已入库附件和 embed 图的补下载；图片缩略图；dashboard 里的图片预览和大图、音视频播放、文件下载；单个文件上限和总容量设置；失败重试；本地开发用的本地目录存储。
- 不包含：第三方网站托管的 embed 图（继续按 spec 0003 直接加载）；头像、服务器图标、表情；视频封面和视频转码；删除 R2 里的文件（任何情况下都不自动删）；附件桶的额外备份；上线前已入库的转发附件（库里只有文件名，没有链接）。

**User stories**：
- 作为唯一的使用者，我希望频道里发的图片和文件被自动存到我自己的存储里，这样几天后 Discord 链接过期了我仍然能看到。
- 作为唯一的使用者，我希望消息被删除或附件被拿掉之后，我仍能看到当时发的图，这样留痕是完整的。
- 作为唯一的使用者，我希望能控制附件最多占多少空间，空间满了能马上知道，而不是悄悄丢东西。

**Acceptance criteria**：
- **AC-1**：开启采集的频道里来了一条带附件的新消息，写入消息的同一个事务里为每个附件建一行 `media`（`kind = 'attachment'`），状态 `pending`。不超过 10 MB 的文件，正常情况下从消息入库到状态变成 `stored` 不超过 15 秒。补采（第 10 项）以后走同一个 `ingest_message`，自动得到同样的行为。
- **AC-2**：转发消息里的每个附件建一行 `media`（`kind = 'forwarded_attachment'`，`slot` 是它在转发里的序号）。embed 的 `image_url` 和 `thumbnail_url` 只有在是 Discord 托管的地址时才建行（`kind = 'embed_image'` 或 `'embed_thumbnail'`，`slot` 是 embed 的序号）。Discord 托管的地址是指：`https` 协议，主机名正好是 `cdn.discordapp.com` 或 `media.discordapp.net`，路径以 `/attachments/` 或 `/ephemeral-attachments/` 开头。其他地址不建行，前端照旧直接加载。
- **AC-3**：每次真正发出下载请求前都再检查一次地址是否 Discord 托管（包括换过的新链接）；不跟随任何重定向，收到 3xx 按 `gone` 处理。服务器不会向 Discord 以外的主机发下载请求。
- **AC-4**：R2 里的文件按 `<频道 ID>/<年月>/<SHA-256>.<扩展名>` 存放，缩略图按 `<频道 ID>/<年月>/thumbs/<SHA-256>.webp`。频道是消息所在的频道（线程用线程自己的 ID），年月是消息发送时间的 UTC 年月（`2026-11`）。同一频道同一个月里内容相同的文件只存一份：第二次下载到同样内容时不再上传，也不增加已用容量，两行 `media` 指向同一个 `stored_file`。不同频道或不同月份的相同文件各存一份。
- **AC-5**：PNG、JPEG、WebP、GIF（取第一帧）、BMP 的图片，长边超过 800 像素时生成长边 800 像素的 WebP 缩略图（GIF 的缩略图是静态的第一帧，大图里看原图），和原图一起存；长边不超过 800 像素的不生成，`has_thumb` 为假，消息里直接用原图。图片解不开或超过 2500 万像素时，只存原图，不生成缩略图，不算失败。SVG、视频、音频和其他文件不生成缩略图。同一时间最多只生成一张缩略图。
- **AC-6**：Discord 给出的大小超过「单个文件上限」时，不下载，状态记为 `too_large`。不知道大小的（embed 图）边下边数，超过上限就中止，记为 `too_large`，并在 `aborted_at_bytes` 记下中止时的上限。
- **AC-7**：已用容量（所有 `stored_file` 的原文件加缩略图大小之和）已经达到「总容量上限」时，不下载、不换链接，直接记为 `no_space`。还没满时照常下载；算完哈希后，内容已经存过的直接记 `stored`（不占空间），否则已用容量加上这个文件（含缩略图）超过上限时记为 `no_space`，不上传。同时有 3 个下载在跑，所以实际用量最多超出上限 3 个文件。只要存在 `no_space` 的行，图标栏的设置入口显示红点（`aria-label` 是「附件空间已满」），设置页的附件存储区块标红并说明「空间已满，新附件没有保存」。
- **AC-8**：设置页有「附件存储」区块，显示：单个文件上限、总容量上限、已用容量（GB，一位小数）和占比、各状态的数量（待保存、已保存、失败、超过上限、空间不足、已失效）。两个上限可以修改：单个文件 1 MB 到 1024 MB，总容量 1 GB 到 10000 GB，用整数 MB 和 GB 输入，超出范围时不保存并显示错误。默认值 50 MB 和 50 GB。保存后几秒内生效，不重启 collector。
- **AC-9**：调高单个文件上限后，`declared_size_bytes` 不超过新上限的 `too_large` 行，以及大小未知且 `aborted_at_bytes` 小于新上限的 `too_large` 行，重新变成 `pending`。保存设置时只要已用容量低于新的总容量，所有 `no_space` 行重新变成 `pending`（排到之后仍然放不下的会再次变成 `no_space`）。
- **AC-10**：下载或上传遇到可以重试的失败（超时、连接错误、5xx、429、R2 写入失败）时，`attempts` 加 1，按 1 分钟、5 分钟、30 分钟、2 小时退避，第 5 次失败后状态记为 `failed`。用 10 分钟内刚换过的链接仍然返回 403 也按可以重试的失败处理（最后落到 `failed`，可以用「重试全部失败」救回）。用刚换过的链接返回 404、或返回 3xx 和其他 4xx 时，记为 `gone`，不再自动重试。1 分钟内有超过 10 个不同的行收到 403 时，下载任务整体暂停 30 分钟（防止 Discord 拦截服务器 IP 时把整个库存都打成失败），暂停期间不领取新行，日志记 `media.paused`。`last_error` 只存错误代码（例如 `timeout`、`http_404`、`r2_put`），不存链接和响应内容。
- **AC-11**：链接快过期（链接里的 `ex` 参数表示的时间距现在不到 5 分钟）或已过期，或者用 10 分钟内没换过的链接下载得到 403、404 时，把这一行标成需要换链接（`needs_refresh`），先换新链接再下载；换链接不计入 `attempts`。换链接用已登录的小号会话调用 Discord 的 refresh-urls 接口，每批最多 50 个链接，两批之间至少隔 30 秒，每小时最多 600 个，收到 429 按 Discord 给的等待时间退避。账号不在线（没有可用的网关会话）时不换链接，这些行等账号上线再处理。只有 Discord 返回 2xx 且响应能解析时，没被返回新链接的那一行才记为 `gone`；5xx、网络错误、401、403、整批 400 或响应解析不了时，这一批的行都不动，只让换链接循环退避。
- **AC-12**：功能上线时，迁移为库里已有的每个 `attachment` 和每个 Discord 托管的 embed 图建一行 `pending` 的 `media`，collector 之后自动补下载（这些旧链接大多已过期，走 AC-11 的换链接节奏）。下载队列按 `message_id` 从大到小处理，所以新消息的附件总是排在补下载前面，补下载从最近的消息往回补。
- **AC-13**：消息被删除、附件在编辑时被拿掉，对应的 `media` 照样下载和保留。`message` 被编辑导致某个 embed 序号上的 Discord 托管图片换了（链接去掉查询参数后不同），那一行重置为 `pending` 并清空 `file_id`，按新链接重新下载。
- **AC-14**：`GET /api/media/{id}` 在已登录时返回 302，跳到 R2 上这个文件 1 小时有效的签名链接，并带 `Cache-Control: private, max-age=3000`，浏览器 50 分钟内再次显示同一张图不再请求 api。`?variant=thumb` 跳到缩略图；没有缩略图时跳到原图。内容类型在白名单里的（`image/png`、`image/jpeg`、`image/webp`、`image/gif`、`image/bmp`、`image/avif`、`video/mp4`、`video/webm`、`audio/*`）以 `inline` 打开，其他文件（包括 SVG）以 `attachment` 加原文件名下载。`?download=1` 时不管类型一律以 `attachment` 加原文件名下载。没登录返回 401；行不存在或还没 `stored` 返回 404。
- **AC-15**：消息里的附件按状态显示：`stored` 的图片在固定尺寸的框里显示缩略图（框的尺寸按已存的宽高算，加载前后行高不变），点开是原图大图，有「下载原文件」（指向 `?download=1`）；`stored` 的视频和音频直接显示播放器（`preload="metadata"`）；其他 `stored` 文件显示文件条，点击下载。`pending` 显示文件条加「保存中」；`too_large` 显示「超过上限，未保存」；`no_space` 显示「空间不足，未保存」；`failed` 显示「保存失败」和一个「重试」按钮；`gone` 显示「Discord 上已失效」。转发块里的附件和 Discord 托管的 embed 图同样处理。没有 `media` 行的附件（理论上不应出现）按原来的文件条显示。前端不再从 Discord 原链接加载任何附件。
- **AC-16**：某行变成 `stored`、`failed`、`gone`、`too_large` 或 `no_space` 时，和状态写入同一个事务里发 `message_changed`（spec 0008 已有的事件），打开着的频道页自动刷新这条消息。
- **AC-17**：消息里的「重试」按钮把那一行（`failed` 或 `gone`）改回 `pending`、`attempts` 清零；设置页的「重试全部失败」把所有 `failed` 行改回 `pending`（不含 `gone`）。`too_large` 和 `no_space` 不能手动重试，只能通过改设置重新排队，接口返回 409。
- **AC-18**：collector 停止或崩溃时正在下载的文件，在下次启动时立刻放回队列，不计入 `attempts`，不需要等待超时；临时目录在启动时清空，崩溃留下的半截文件不会越积越多。同一个文件被上传两次也不会出错（R2 里的文件名就是内容哈希，重复上传结果一样）。正在下载的行被重置（AC-13）或手动重试后，那次下载的结果被丢弃，不会把旧文件写回去。
- **AC-22**：下载任务遇到意外异常时记 `error` 日志，等 60 秒后继续运行，不会悄悄停掉，也不会让 collector 进程退出（退出重启要消耗登录额度）。下载任务每 30 秒写一次自己的心跳；首页系统状态显示「附件下载」最近心跳的相对时间，collector 进程心跳正常而下载任务心跳超过 180 秒没更新时，这一行标红并显示「附件下载任务无响应」；处于 AC-10 的暂停期时显示「已暂停到 <时间>」。
- **AC-19**：R2 桶是私有的，不开公开访问。collector 用一个只作用于这个桶的读写 token，api 用一个只作用于这个桶的只读 token；两个进程各自只拿到自己那一组密钥。日志、错误响应和数据库里都不出现 R2 密钥、签名链接或 Discord 链接的查询参数（记日志时只留主机名和路径）。
- **AC-20**：`MEDIA_STORAGE=local` 时文件写到 `MEDIA_LOCAL_DIR`（默认 `./media-dev`），`GET /api/media/{id}` 直接返回文件内容（同样的 `Content-Type`、`Content-Disposition` 和缓存头），本地开发不需要 R2。`MEDIA_STORAGE=r2` 时缺少任何一个 R2 配置，对应进程启动时报错退出。`MEDIA_STORAGE` 没设置时也报错退出。
- **AC-23**：频道的采集开关（`channel.collect_enabled`）关掉后，这个频道里还没保存的附件停止处理：不领取、不换链接、不计入 `attempts`，状态保持 `pending`；关掉那一刻已经在下载的（最多 3 个）照常完成；已经 `stored` 的不受影响。重新打开后自动继续，不需要手动重试。线程看它自己的开关（关掉父频道时线程会被一起关掉，spec 0006）。设置页「待保存」数量旁边显示其中有多少个「所在频道已关闭」。
- **AC-21**：新接口都要求登录；改动的写接口走现有的 Origin 检查；接口改动后 OpenAPI 和前端类型同步；后端 ruff、pyright、pytest 和前端 Biome、typecheck、Vitest、build 全部通过。

## Decision

**Chosen option**: Option 2：私有 R2 桶加 collector 内的下载队列，api 跳转到签名链接。

collector 在 `ingest_message` 的同一个事务里给每个要保存的东西建 `media` 行，主进程里一个独立的下载任务按 `message_id` 从新到旧领取、下载、算哈希、生成缩略图、上传 R2；api 只负责验登录后签发 1 小时的签名链接。

**Implementation skills**：`fastapi`（`fastapi/fastapi`，`.claude/skills/fastapi/`）· `postgresql-table-design`（`wshobson/agents`，`.claude/skills/postgresql-table-design/`）· `tanstack-query`（`tanstack-skills/tanstack-skills`，`.claude/skills/tanstack-query/`）· `shadcn`（`shadcn-ui/ui`，`.claude/skills/shadcn/`）· `cloudflare`（`cloudflare/skills`，`.claude/skills/cloudflare/`）· `python-testing-patterns`（`wshobson/agents`，`.claude/skills/python-testing-patterns/`）· `vitest`（`antfu/skills`，`.claude/skills/vitest/`）· `security-review`（`getsentry/skills`，`.claude/skills/security-review/`）

## Feature design

### 新依赖

| 进程 | 包 | 用途 |
|---|---|---|
| backend | `boto3`，开发依赖 `mypy-boto3-s3`（只用它的类型） | R2 的上传、读取元数据、签发签名链接 |
| backend | `Pillow` | 缩略图 |
| backend | `httpx`（`fastapi[standard]` 已带，改成直接依赖） | 从 Discord CDN 流式下载 |

装之前核对当前版本。`boto3` 是同步库：上传放到 `asyncio.to_thread` 里跑；签发签名链接是纯本地计算，直接调用。`boto3` 只在 `harvester.media.storage` 一处使用，对外是带类型的函数。

### 代码位置

- `backend/src/harvester/media/`（新包，两个进程共用的基础设施）：
  - `urls.py`：纯函数。`is_discord_hosted(url)`、`link_expires_at(url)`（解析 `ex`，十六进制秒）、`strip_query(url)`、`discord_attachment_id(url)`（路径里 `/attachments/<channel>/<id>/<name>` 的 `<id>`）、`file_prefix(channel_id, created_at)`、`object_key(prefix, sha256, filename)`、`thumb_key(sha256)`。
  - `policy.py`：纯函数。`next_backoff(attempts)`、`classify_download_error(...)`（返回 `retry`、`refresh`、`gone`）、`admit(declared_size, max_file, used, quota)`（返回 `ok`、`too_large`、`no_space`）、`content_disposition(content_type, filename)`。
  - `storage.py`：`MediaStore` 协议（`put_file`、`exists`、`presign_get`、`open_local`），三个实现：`R2Store`、`LocalStore`、测试用的 `MemoryStore`。由进程入口按配置构造后显式传入。
  - `thumbnail.py`：`make_thumbnail(path) -> Thumbnail | None`。先 `Image.open` 读尺寸，`w * h > THUMB_MAX_PIXELS`（常量 25 000 000）就返回 `None`，不解码；JPEG 先 `im.draft("RGB", (1600, 1600))` 再 `thumbnail()`。不修改 `Image.MAX_IMAGE_PIXELS`（Pillow 超过它只警告，到 2 倍才报错，靠它挡不住；保留默认值当兜底）。
  - `config` 部分：`load_media_settings() -> LocalMediaSettings | R2MediaSettings`，先读 `MEDIA_STORAGE`，再实例化对应的类，字段都不是可选的，pyright 可以收窄。
- `backend/src/harvester/db/media.py`：`sync_media_for_message`（`ingest_message` 内调用）、`claim_batch`、`release_claims_on_start`、`record_stored`、`record_retry`、`record_terminal`、`get_media_settings`、`update_media_settings`、`retry_media`、`retry_all_failed`、`media_usage`。
- `backend/src/harvester/collector/media_worker.py`：下载任务，挂在 `collector/main.py` 的任务列表里（和 `process-heartbeat` 并列，名字 `media-worker`）。
- `backend/src/harvester/collector/discord_client.py`：新增带类型的边界函数 `refresh_attachment_urls(client, urls) -> RefreshResult`，用当前已登录客户端的 HTTP 会话调用 `POST /attachments/refresh-urls`。`RefreshResult` 是判别联合：`Refreshed(mapping)`（2xx 且能解析）、`RateLimited(retry_after)`、`Unavailable(reason)`（其他所有情况）。`GatewaySession` 协议扩出 `refresh_attachment_urls(urls)`。
- `backend/src/harvester/collector/supervisor.py`：`Deps` 新增 `gateway: GatewayHandle`（一个显式传入的可变容器，和已有的 `wake` 事件同类，不是模块级状态）。写库成功后在 `_on_ready` 和 `_on_resumed` 里放入当前 `GatewaySession`；在 `_on_disconnected`、`_on_retry` 和 `run_session` 的 `finally`（`client.close()` 之前）里清空。下载任务判断「在线」只看句柄里有没有会话，不读数据库里的账号状态。
- `backend/src/harvester/collector/main.py`：`media_queue` 的监听照现有写法挂在锁连接上（`lock_conn.add_listener`），回调只设 `media_wake` 事件，事件通过参数传给下载任务。
- `backend/src/harvester/api/media_routes.py`：本 spec 的接口。
- 前端：`web/src/components/messages/`（`attachment-chip.tsx` 扩展、新 `media-image.tsx`、`media-player.tsx`、`media-lightbox.tsx`、`media-status.tsx`），`web/src/components/settings/` 下新增附件存储区块，`web/src/lib/api/` 下新增调用。

### 数据模型

一个只新增的迁移，加三张表，不改已有的表。

**`stored_file`**（R2 里的一个真实文件，按内容只存一份）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `bigint GENERATED ALWAYS AS IDENTITY` | 否 | 主键 |
| `prefix` | `text` | 否 | `<频道 ID>/<YYYY-MM>`，由 `file_prefix` 算出 |
| `sha256` | `bytea` | 否 | `CHECK (octet_length(sha256) = 32)` |
| `size_bytes` | `bigint` | 否 | 原文件大小，`CHECK (size_bytes >= 0)` |
| `content_type` | `text` | 否 | 见下面「内容类型」 |
| `object_key` | `text` | 否 | `<prefix>/<64 位十六进制>.<扩展名>`，唯一；扩展名取 `media.filename` 的扩展名，转小写，只保留字母和数字，最多 10 个字符，取不到就不加 |
| `width`、`height` | `integer` | 是 | 图片原始尺寸（Pillow 读出）；视频沿用 Discord 给的尺寸 |
| `thumb_object_key` | `text` | 是 | `<prefix>/thumbs/<64 位十六进制>.webp` |
| `thumb_size_bytes` | `bigint` | 是 | |
| `thumb_width`、`thumb_height` | `integer` | 是 | |
| `created_at` | `timestamptz` | 否 | 默认 `now()` |

约束：`uq_stored_file_prefix_sha256`：`UNIQUE (prefix, sha256)`，这就是去重的范围；`thumb_object_key`、`thumb_size_bytes`、`thumb_width`、`thumb_height` 要么全空，要么全有（`CHECK`）。

**`media`**（每个要下载的东西一行）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `bigint GENERATED ALWAYS AS IDENTITY` | 否 | 主键 |
| `message_id` | `bigint` → `message.id` | 否 | |
| `kind` | `text` | 否 | `attachment`、`forwarded_attachment`、`embed_image`、`embed_thumbnail` |
| `attachment_id` | `bigint` → `attachment.id` | 是 | `kind = 'attachment'` 时必须有，其他时候必须为空 |
| `slot` | `integer` | 是 | `kind = 'attachment'` 时为空，其他时候必须有，`>= 0` |
| `discord_attachment_id` | `bigint` | 是 | 从链接里解析；解析不出时为空 |
| `filename` | `text` | 否 | 附件用 Discord 给的；embed 图用链接路径最后一段 |
| `declared_content_type` | `text` | 是 | Discord 给的 |
| `declared_size_bytes` | `bigint` | 是 | Discord 给的；embed 图为空 |
| `declared_width`、`declared_height` | `integer` | 是 | Discord 给的，保存前用来固定预览框尺寸 |
| `source_url` | `text` | 否 | 最近一次拿到的链接（含签名） |
| `status` | `text` | 否 | `pending`、`stored`、`failed`、`too_large`、`no_space`、`gone` |
| `attempts` | `smallint` | 否 | 默认 0，可以重试的失败次数 |
| `next_attempt_at` | `timestamptz` | 否 | 默认 `now()` |
| `claimed_at` | `timestamptz` | 是 | 下载任务领取时填上，处理完清空；写结果时用它确认这次领取仍然有效 |
| `needs_refresh` | `boolean` | 否 | 默认 `false`；为真时等换链接循环处理，下载领取跳过它 |
| `refreshed_at` | `timestamptz` | 是 | 最近一次换到新链接的时间 |
| `aborted_at_bytes` | `bigint` | 是 | 大小未知的文件因超过上限中止时的上限值 |
| `last_error` | `text` | 是 | 错误代码 |
| `file_id` | `bigint` → `stored_file.id` | 是 | `status = 'stored'` 时必须有，其他时候必须为空 |
| `created_at` | `timestamptz` | 否 | 默认 `now()` |
| `stored_at` | `timestamptz` | 是 | |

约束和索引（全部写明名字）：
- `CHECK` `kind`、`status` 的取值；`attachment_id` 和 `slot` 与 `kind` 的对应关系；`file_id` 与 `status` 的对应关系。
- `uq_media_attachment`：`UNIQUE (attachment_id)`（PostgreSQL 的唯一约束本来就允许多个空值）。
- `uq_media_message_slot`：`UNIQUE (message_id, kind, slot) WHERE kind <> 'attachment'`。
- `ix_media_queue`：`(message_id DESC) WHERE status = 'pending' AND claimed_at IS NULL AND NOT needs_refresh`，给领取用。
- `ix_media_refresh`：`(message_id DESC) WHERE status = 'pending' AND needs_refresh`，给换链接循环用。
- `ix_media_problem`：`(status) WHERE status IN ('too_large', 'no_space', 'failed')`，给系统状态的 `EXISTS` 和 AC-9 的重新排队用。
- `ix_media_message`：`(message_id)`，给消息接口用（`uq_media_message_slot` 只覆盖非附件行，所以单独建）。
- `ix_media_file`：`(file_id)`，外键用。

**`media_settings`**（单行）

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | `smallint` | 否 | 主键，`CHECK (id = 1)` |
| `max_file_bytes` | `bigint` | 否 | 默认 52 428 800（50 MB），`CHECK` 在 1 MB 到 1024 MB 之间 |
| `quota_bytes` | `bigint` | 否 | 默认 53 687 091 200（50 GB），`CHECK` 在 1 GB 到 10000 GB 之间 |
| `updated_at` | `timestamptz` | 否 | 默认 `now()` |

本 spec 的 MB 和 GB 都按 1024 进位。迁移插入 `id = 1` 的默认行。

**`collector_heartbeat`**（spec 0009 已有，只新增一列）：`media_worker_seen_at timestamptz NULL`，下载任务每 30 秒写一次；`media_paused_until timestamptz NULL`，AC-10 的暂停结束时间，没有暂停时为空。

**关系**：`message 1 ─ N media`；`attachment 1 ─ 0..1 media`；`stored_file 1 ─ N media`；`media_settings` 独立。

**迁移里的补建**（AC-12）：同一个迁移里用 SQL 补建行：
- 每个 `attachment` 一行 `kind = 'attachment'`，`source_url`、文件名、类型、大小、宽高从 `attachment` 复制，`discord_attachment_id = attachment.id`。
- `message.embeds` 用 `jsonb_array_elements ... WITH ORDINALITY` 展开，`image_url`、`thumbnail_url` 满足 AC-2 规则的各建一行，`slot` 是序号减 1，`filename` 取路径最后一段，`discord_attachment_id` 从路径里取（取不到就为空）。
- 链接里的 `ex` 已经过期（或距现在不到 5 分钟）的行直接写 `needs_refresh = true`，省掉一次领取再放回。
- `ON CONFLICT DO NOTHING`，迁移可以重跑。

### 状态变化

```
pending ──(下载成功)──────────────▶ stored
pending ──(可重试的失败，第 1 到 4 次)─▶ pending（next_attempt_at 退避）
pending ──(第 5 次可重试的失败，包括新链接仍 403)─▶ failed
pending ──(需要换链接)─────────────▶ pending（needs_refresh = true，换到后清掉）
pending ──(新链接仍 404，或 Discord 正常响应但没给新链接，或 3xx、其他 4xx)─▶ gone
pending ──(超过单个文件上限)──────▶ too_large
pending ──(超过总容量)───────────▶ no_space
failed、gone ──(手动重试)─────────▶ pending（attempts 清零）
too_large、no_space ──(保存设置，满足 AC-9)─▶ pending
stored、任何状态 ──(embed 序号上的图片换了)─▶ pending（清空 file_id，attempts 清零）
```

`stored` 不会因为别的原因离开；没有任何流程删除 `media` 或 `stored_file`。

### 写入 `media` 行（`sync_media_for_message`）

在 `ingest_message` 的事务里，写完附件之后调用，用快照（不是库里的行）决定要建什么：
- 快照 `attachments_known` 为真时，快照里每个附件 `INSERT ... ON CONFLICT (attachment_id) DO UPDATE SET source_url = EXCLUDED.source_url`（只在状态不是 `stored` 时更新链接；新链接更新鲜）。附件被拿掉不动它的 `media` 行。
- 快照 `attachments_known` 为真且有转发时，为每个转发附件 `INSERT ... ON CONFLICT (message_id, kind, slot) WHERE kind <> 'attachment' DO UPDATE SET source_url = EXCLUDED.source_url WHERE media.status <> 'stored'`。因为 `uq_media_message_slot` 是部分唯一索引，SQLAlchemy 里要带 `index_where=text("kind <> 'attachment'")`，做法同 `ingest.py` 的 `open_realtime_coverage`。每次入库都同步，不区分是否第一次写入，所以实时和补采并发写同一条消息也不会漏行。`MessageSnapshot` 新增 `forwarded_attachments: tuple[AttachmentSnapshot, ...] = ()`，由 `snapshot_message` 从 `message_snapshots[0].attachments` 填；不改 `ForwardedData` 的 JSON 结构（`attachment_names` 照旧）。
- 每个 embed 的 `image_url`、`thumbnail_url` 满足 AC-2 时：没有行就建；已有行且 `strip_query` 后的地址不同，按 AC-13 重置；相同就只更新 `source_url`（非 `stored` 时）。embed 被拿掉或序号变少，已有行不动。embed 部分不受 `attachments_known` 限制。
- 重置是一条 `UPDATE`，同时设 `status = 'pending'`、`file_id`、`stored_at`、`last_error`、`claimed_at`、`refreshed_at`、`aborted_at_bytes` 为空，`attempts = 0`，`needs_refresh = false`，`next_attempt_at = now()`，否则 `status` 和 `file_id` 的 `CHECK` 会失败。清空 `claimed_at` 让正在进行的那次下载的结果作废（见下载任务第 7 步）。
- 有任何新建或重置的行时，同一个事务里 `pg_notify('media_queue', '')`。

`ingest_message` 的插入分支和更新分支都要调用（插入分支在写完附件后会提前返回，两处都要接上）。返回 `stale` 或 `deleted` 时不调用（和附件同步的规则一致），所以消息被删除之后，再收到的编辑不会给它新增 `media` 行。

### 下载任务（`media_worker`）

- 启动：先清空 `MEDIA_TMP_DIR`；再 `release_claims_on_start`：把所有 `claimed_at IS NOT NULL` 的行清空 `claimed_at`、`next_attempt_at = now()`（AC-18）。collector 有单实例锁，所以这时不可能有别的进程在处理它们。
- 唤醒：`media_wake` 事件（锁连接上 `media_queue` 的监听设它；已有的 `on_collect_changed` 回调也顺便设它，频道重新打开后马上继续）；没有通知时每 60 秒检查一次；处理完一批立即再取。
- 健壮性（AC-22）：下载循环、换链接循环、心跳循环各自的最外层包 `except Exception`，记 `error` 日志（带 `error_type`）后等 60 秒继续。三个循环都不放进 `main.py` 的 `waiters`，出错不会让进程退出。心跳循环每 30 秒写 `collector_heartbeat.media_worker_seen_at`。
- 暂停（AC-10）：内存里记最近 1 分钟收到 403 的不同 `media_id`；超过 10 个时设暂停到 `now() + 30 分钟`，写 `collector_heartbeat.media_paused_until`，记 `media.paused`。暂停期间不领取；到期后清空这一列。
- 领取：一个短事务 `SELECT ... WHERE status = 'pending' AND claimed_at IS NULL AND NOT needs_refresh AND next_attempt_at <= now() ORDER BY message_id DESC, id LIMIT n FOR UPDATE OF media SKIP LOCKED`，并 `JOIN message` 和 `channel`，只取 `channel.collect_enabled` 为真的行（AC-23），填上 `claimed_at = now()` 后提交，并把每行的 `claimed_at` 值留在内存里。`n` 是空闲的槽位数，最多同时 3 个在下载（`asyncio.Semaphore(3)`）。
- 每一行的处理（网络操作都在事务外，最后一个短事务写结果）：
  1. 读 `media_settings` 和当前已用容量。`declared_size_bytes` 超过单个文件上限 → `too_large`；已用容量已经达到总容量 → `no_space`（AC-7，满了就不下载也不换链接）。
  2. 链接快过期或已过期 → 设 `needs_refresh = true`、清空 `claimed_at`，交给换链接循环。
  3. `is_discord_hosted(source_url)` 不成立 → `gone`（AC-3，正常不会发生）。
  4. 流式下载到 `MEDIA_TMP_DIR` 下的 `NamedTemporaryFile(delete=True)`。httpx 客户端：`follow_redirects=False`、`trust_env=False`（不受代理环境变量影响）、请求头 `Accept-Encoding: identity`（计数的就是文件本身的字节）；连接超时 10 秒，读空闲超时 30 秒，单个文件总时长 10 分钟。边写边算 SHA-256，超过单个文件上限立刻中止 → `too_large`，`aborted_at_bytes` 记当时的上限。
  5. 用这一行消息的 `channel_id` 和 `created_at` 算出 `prefix`；已有同 `prefix` 同哈希的 `stored_file` → 直接写 `stored`，不上传（AC-4）。
  6. 否则：图片按 AC-5 生成缩略图（单独的 `asyncio.Semaphore(1)`，不占下载的 3 个槽，放到 `asyncio.to_thread`）；已用容量加上原文件和缩略图大小超过总容量 → `no_space`，不上传；否则上传原文件和缩略图，`INSERT stored_file ... ON CONFLICT (prefix, sha256) DO NOTHING`，再按（`prefix`、`sha256`）读回 `id`，写 `stored`。
  7. 写结果的每一条 `UPDATE`（`record_stored`、`record_retry`、`record_terminal`、设 `needs_refresh`）都带 `WHERE id = :id AND claimed_at = :claimed_at`。影响 0 行说明这一行在下载期间被重置或手动重试过，丢弃这次结果，不写库（上传到 R2 的文件按哈希命名，留着无害）。
  8. 每个最终状态和 `stored` 都在同一个事务里 `notify_message_changed(channel_id, (message_id,))`（AC-16）。
- 失败分类（`classify_download_error(response_or_error, row, now)`，纯函数，用行里的 `refreshed_at` 判断「刚换过」，即 10 分钟以内）：

| 情况 | 结果 |
|---|---|
| 超时、连接错误、5xx、429、R2 上传或读取失败 | `retry`：`attempts + 1`；第 1 到 4 次按 1、5、30、120 分钟退避，第 5 次 `failed` |
| 403、404，且没有刚换过链接 | `refresh`：设 `needs_refresh = true`，不加 `attempts` |
| 403，且刚换过链接 | `retry`（同第一行），并计入暂停判断 |
| 404，且刚换过链接 | `gone` |
| 3xx、其他 4xx | `gone` |

- 换链接循环：从库里取 `WHERE status = 'pending' AND needs_refresh ORDER BY message_id DESC LIMIT 50`，同样只取所在频道 `collect_enabled` 为真的行（走 `ix_media_refresh`，重启不丢）。只在 `GatewayHandle` 里有会话时调用；两批之间至少隔 30 秒；内存里记最近一小时换过的链接数，到 600 就等到窗口腾出来。按 `RefreshResult` 处理：
  - `Refreshed`：返回了新链接的行，一条 `UPDATE` 同时写 `source_url`、`needs_refresh = false`、`refreshed_at = now()`、`next_attempt_at = now()`；这一批里没被返回的行记 `gone`。有更新就发 `media_queue`。
  - `RateLimited`：按 `retry_after` 等待，行不动。
  - `Unavailable`（5xx、网络错误、401、403、整批 400、解析失败）：行不动，循环按 1、5、30 分钟退避，成功一次后复原。
  换链接请求和普通 Discord 请求一样走 discord.py-self 的限速。
- 停止：SIGTERM 时取消三个循环，`finally` 里用 `asyncio.shield` 包一个短事务，尽力把本进程领取的行清空 `claimed_at`；做不到也没关系，下次启动会释放。

### 内容类型

`stored_file.content_type` 依次取：Discord 给的 `declared_content_type`；按文件名猜（`mimetypes`）；`application/octet-stream`。Pillow 能打开的文件，以 Pillow 识别的格式为准（例如扩展名是 `.png` 实际是 JPEG）。

`content_disposition(content_type, filename, download)`：`download` 为假且类型在白名单 `INLINE_TYPES`（`image/png`、`image/jpeg`、`image/webp`、`image/gif`、`image/bmp`、`image/avif`、`video/mp4`、`video/webm`，以及 `audio/` 开头的类型）里时返回 `inline`；其他返回 `attachment; filename="<ASCII 兜底名>"; filename*=UTF-8''<百分号编码的文件名>`。文件名先去掉控制字符（含回车和换行）、`"` 和 `\`；ASCII 兜底名把非 ASCII 字符换成 `_`，清理后为空时用 `file`。文件名用 `media.filename`（同一个文件可能有多个名字，取请求的那一行的）。白名单是显式列表，不按 `image/` 前缀放行；文件开在 R2 的域名上，类型又由签名链接强制指定，不会被浏览器当成网页执行。

### 接口

| Endpoint | Method | Key inputs | Key outputs | Auth | Key errors |
|---|---|---|---|---|---|
| `/api/media/{media_id}` | GET | `media_id`：int（路径），`variant`：`original` 或 `thumb`（查询，默认 `original`），`download`：bool（查询，默认假） | R2：302 跳到签名链接；本地：200 文件内容。都带 `Cache-Control: private, max-age=3000` | 会话 | 401 未登录；404 `media_not_found`（不存在或不是 `stored`）；422 参数非法 |
| `/api/media/{media_id}/retry` | POST | `media_id` | `MediaOut` | 会话加 Origin 检查 | 404 `media_not_found`；409 `media_not_retryable`（不是 `failed` 或 `gone`） |
| `/api/media/retry-failed` | POST | 无 | `{ "requeued": int }` | 会话加 Origin 检查 | 401 |
| `/api/media/settings` | GET | 无 | `MediaSettingsOut` | 会话 | 401 |
| `/api/media/settings` | PUT | `max_file_mb`：int（1 到 1024），`quota_gb`：int（1 到 10000） | `MediaSettingsOut` | 会话加 Origin 检查 | 422 超出范围 |
| `/api/channels/{id}/messages`（已有） | GET | 不变 | `MessageOut` 新增 `media: list[MediaOut]` | 不变 | 不变 |
| `/api/system/status`（已有） | GET | 不变 | 新增 `media: MediaWorkerStatusOut` | 不变 | 不变 |

- 签名链接：`R2Store.presign_get(object_key, expires=3600, content_type, content_disposition)`，用 `generate_presigned_url('get_object', Params={..., 'ResponseContentType': ..., 'ResponseContentDisposition': ...})`，所以浏览器拿到的响应头由 api 决定，不依赖上传时写的元数据。`variant=thumb` 而没有缩略图时用原图；缩略图的类型固定是 `image/webp`。
- `R2Store` 的 boto3 客户端：`endpoint_url=https://<MEDIA_R2_ACCOUNT_ID>.r2.cloudflarestorage.com`，`region_name="auto"`，`Config(signature_version="s3v4", request_checksum_calculation="when_required", response_checksum_validation="when_required")`。上传一律用 `upload_file(临时文件路径, ...)` 加 `TransferConfig(max_concurrency=2)`，大文件自动分段，不把整个文件读进内存。
- `MediaWorkerStatusOut`：`storage_full`（存在 `no_space` 行，走 `ix_media_problem`）、`last_seen_at`（`collector_heartbeat.media_worker_seen_at`）、`stale`（collector 进程心跳正常而 `last_seen_at` 为空或超过 180 秒）、`paused_until`（`media_paused_until`，已过期按空返回）。
- `MediaOut`：`id`（字符串）、`kind`、`attachment_id`（字符串，可空）、`slot`（可空）、`status`、`filename`、`content_type`（`stored` 时用 `stored_file.content_type`，否则用 `declared_content_type`）、`size_bytes`（`stored` 时实际大小，否则 Discord 给的，可空）、`width`、`height`（`stored` 时用 `stored_file` 的，否则用 `declared_*`，可空）、`has_thumb`。消息接口对当前页的消息 ID 一次查出 `media`（走 `ix_media_message`），`LEFT JOIN stored_file` 取这些字段，不逐条查。
- `media` 列表按 `kind`、`attachment_id`、`slot` 排序，包括已被拿掉的附件的行（前端只显示和 `attachments`、`forwarded`、`embeds` 对得上的）。
- `MediaSettingsOut`：`max_file_mb`、`quota_gb`、`used_bytes`、`counts`（六个状态各一个整数）、`pending_channel_off`（`pending` 中所在频道已关闭的数量）、`storage_full`（存在 `no_space` 行）。
- `PUT /api/media/settings` 在同一个事务里：写设置；按 AC-9 把 `too_large`、`no_space` 改回 `pending`；有改动就 `pg_notify('media_queue', '')`。
- 重试接口在同一个事务里改状态、清 `attempts`、`last_error`、`claimed_at`、`needs_refresh`、`refreshed_at`，`next_attempt_at = now()`，发 `media_queue` 和 `message_changed`。重试的行如果当时正被领取（理论上 `failed` 和 `gone` 不会），清空 `claimed_at` 会让那次结果作废。
- 改接口后按 `AGENTS.md` 导出 OpenAPI 并重新生成前端类型。

### 前端

- `to-message-view.ts`：把 `media` 按位置挂到视图上：`AttachmentView.media`、转发块新增 `attachments: readonly ForwardedAttachmentView[]`（`filename` 来自 `attachment_names[slot]`，`media` 来自 `kind = 'forwarded_attachment'` 的同序号行；上线前的转发没有行，只显示文件名）、`EmbedView.imageMedia`、`EmbedView.thumbnailMedia`。
- 图片：固定尺寸框（宽度上限沿用 spec 0003 的 400px，按 `width`、`height` 等比；没有尺寸时 16:9 框加 `object-contain`），`src="/api/media/{id}?variant=thumb"`，`loading="lazy"`。点击打开 `MediaLightbox`（shadcn `Dialog`），显示 `variant=original`，带「下载原文件」链接（`/api/media/{id}`，文件名由响应头决定）。
- 视频、音频：`<video controls preload="metadata">`、`<audio controls preload="metadata">`，`src="/api/media/{id}"`；视频框尺寸同图片。
- 其他文件和未保存的状态：`AttachmentChip` 加状态文字和「重试」按钮（只在 `failed`、`gone` 时出现，调重试接口后乐观改成「保存中」）。
- embed：`imageMedia` 或 `thumbnailMedia` 存在时按它的状态显示（`stored` 用自己的地址，其他显示状态文字）；不存在时按 spec 0003 直接加载原地址（第三方图片）。
- 设置页「附件存储」区块：两个数字输入（MB、GB）和保存按钮、已用容量和占比、六个状态数量、「重试全部失败」按钮（失败数为 0 时禁用）。`storage_full` 时区块用 error 色调。接口错误走现有的统一错误转换。
- 图标栏设置入口：`media.storage_full` 为真时显示红点，`aria-label` 是「附件空间已满」。
- 首页系统状态新增「附件下载」一行：`last_seen_at` 的相对时间（悬停看完整时间）；`stale` 时用 error 色调并显示「附件下载任务无响应」；`paused_until` 有值时显示「已暂停到 <本地时间>」。

### Value sourcing

| Action | Value produced / displayed | Source |
|---|---|---|
| 建行 | 要下载的附件 | `MessageSnapshot.attachments`、新增的 `forwarded_attachments`、`embeds[*].image_url`、`thumbnail_url` |
| 建行 | 是否 Discord 托管 | 纯函数 `is_discord_hosted`，规则见 AC-2 |
| 建行 | `slot` | 转发附件在 `message_snapshots[0].attachments` 里的序号；embed 在 `embeds` 里的序号（从 0 起） |
| 建行 | embed 图的文件名 | 链接路径最后一段，URL 解码 |
| 补建 | 旧附件和旧 embed 图 | 迁移里读 `attachment` 和 `message.embeds` |
| 领取 | 是否处理 | `channel.collect_enabled`（经 `media.message_id` → `message.channel_id`），每次领取时现查 |
| 设置页 | 因频道关闭而暂停的数量 | `pending` 行中所在频道 `collect_enabled` 为假的数量 |
| 领取 | 处理顺序 | `media.message_id` 从大到小，再按 `id` |
| 准入 | 单个文件上限、总容量 | `media_settings.max_file_bytes`、`quota_bytes` |
| 准入 | 已用容量 | `SUM(stored_file.size_bytes + COALESCE(thumb_size_bytes, 0))`，下载前和上传前各现查一次 |
| 准入 | 文件大小 | `media.declared_size_bytes`；为空时下载过程中实际计数；上传前用实际大小加缩略图大小 |
| 重新排队 | 未知大小的文件中止时的上限 | `media.aborted_at_bytes` |
| 换链接 | 链接过期时间 | `source_url` 的 `ex` 查询参数（十六进制 Unix 秒）；没有 `ex` 视为未过期 |
| 换链接 | 要换的行 | `media.needs_refresh` |
| 换链接 | 是否刚换过 | `media.refreshed_at` 在 10 分钟以内 |
| 换链接 | 新链接 | Discord refresh-urls 响应里 `refreshed_urls[*].refreshed`，按 `original` 对应回行 |
| 换链接 | 能不能调用 | `Deps.gateway`（`GatewayHandle`）里有会话 |
| 换链接 | 每小时额度 | 换链接循环内存里最近一小时的计数，上限常量 600 |
| 暂停 | 403 计数和暂停时间 | 下载任务内存里最近 1 分钟的 `media_id` 集合；暂停结束时间写 `collector_heartbeat.media_paused_until` |
| 写结果 | 这次领取是否仍有效 | 领取时读回的 `claimed_at`，和库里当前值比较 |
| 退避 | 下次时间 | `next_backoff(attempts)`：1、5、30、120 分钟 |
| 存储 | R2 目录 | `file_prefix(message.channel_id, message.created_at)`，UTC 年月 |
| 存储 | R2 文件名 | `object_key(prefix, sha256, media.filename)`、`thumb_key(prefix, sha256)` |
| 存储 | 内容类型 | 见「内容类型」 |
| 读取 | 签名链接 | `R2Store.presign_get`，有效期 3600 秒 |
| 读取 | `Content-Disposition` | `content_disposition(stored_file.content_type, media.filename, download 查询参数)` |
| 读取 | 浏览器缓存 | 常量 `max-age=3000` |
| 消息行 | 预览框尺寸 | `MediaOut.width`、`height` |
| 消息行 | 状态文字 | `MediaOut.status`，前端映射表 |
| 设置页 | 已用 GB | `used_bytes / 1024³`，一位小数 |
| 设置页 | 占比 | `used_bytes / quota_bytes`，整数百分比 |
| 设置页 | 各状态数量 | `SELECT status, count(*) FROM media GROUP BY status` |
| 图标栏 | 红点 | `/api/system/status` 的 `media.storage_full` = 存在 `status = 'no_space'` 的行 |
| 首页 | 下载任务心跳和暂停 | `collector_heartbeat.media_worker_seen_at`、`media_paused_until`；「无响应」的判断同时用 spec 0009 的进程心跳 |

### Key invariants

- 下载请求只发往 `cdn.discordapp.com` 和 `media.discordapp.net`，不跟随重定向。
- `media.status = 'stored'` 当且仅当 `file_id` 不为空；`kind = 'attachment'` 当且仅当 `attachment_id` 不为空。
- 一个 `attachment` 最多一行 `media`；一条消息同一 `kind` 同一 `slot` 最多一行。
- R2 里的文件名只由频道、年月和内容哈希决定（扩展名取第一次保存时那一行的文件名），同一个键重复上传结果一样，所以上传天然幂等；没有任何代码删除 R2 文件、`stored_file` 或 `media` 行。
- 状态写入和 `message_changed` 通知在同一个事务里。
- 网络操作（下载、上传、换链接）都不在数据库事务里进行。
- 下载任务写结果时，`claimed_at` 必须和领取时一致；重置和手动重试会清空它，所以过时的下载结果永远写不进去。
- `needs_refresh = true` 的行不会被下载领取，只由换链接循环处理。
- 下载任务的异常不会让 collector 进程退出。
- 只有 collector 写 `stored_file` 和 R2；api 只读 R2（签名），只改 `media` 的状态（重试、改设置引起的重新排队）和 `media_settings`。
- 进程之间只通过 PostgreSQL 交流：api 通过 `media_queue` 通知唤醒 collector。

### Security model

- 唯一的使用者是登录了 dashboard 的你；所有新接口都要求会话，写接口加 Origin 检查（spec 0004）。
- R2 桶私有；签名链接 1 小时后作废。链接被复制出去，最多 1 小时内能打开那一个文件。
- 文件在 R2 的域名上打开，和 dashboard 不同源，所以恶意的 HTML 或 SVG 附件拿不到 dashboard 的会话 cookie；不在 `INLINE_TYPES` 白名单里的文件（包括 SVG 和 HTML）一律以下载方式打开，文件名清理过控制字符，不会被注入响应头。本地模式下 api 同源返回文件，加 `X-Content-Type-Options: nosniff` 和 `Content-Security-Policy: sandbox`，同样防止被当成网页执行。
- 两个 R2 token 都只作用于这一个桶；collector 的能读写，api 的只能读。`compose.prod.yml` 只把写 token 给 collector，只把读 token 给 api。
- 换链接用小号的已登录会话，走 discord.py-self 的限速，节奏按 AC-11（每批 50 个、间隔 30 秒、每小时 600 个），降低封号风险。CDN 下载不带登录信息，httpx 不读代理环境变量。
- 解码前先查像素数（2500 万上限）防止压缩炸弹，缩略图一次只做一张；缩略图失败不影响原文件保存。
- 日志不记签名链接和查询参数、R2 密钥、文件内容；记 `media_id`、`kind`、`status`、错误代码、主机名和路径。

### Configuration required

`harvester.config` 新增 `load_media_settings(role)`：先读 `MEDIA_STORAGE`，`local` 时实例化 `LocalMediaSettings`，`r2` 时按进程实例化 `CollectorR2MediaSettings` 或 `ApiR2MediaSettings`。各个类的字段都是必填，api 和 collector 启动时调用。

两个进程都要：
- `MEDIA_STORAGE`：`r2` 或 `local`，必填，没有默认值。
- `MEDIA_LOCAL_DIR`：只在 `local` 时读，默认 `./media-dev`（相对进程工作目录）；加进 `.gitignore`。
- `MEDIA_R2_ACCOUNT_ID`：只在 `r2` 时读，Cloudflare 账号 ID，用来拼 `https://<id>.r2.cloudflarestorage.com`。
- `MEDIA_R2_BUCKET`：只在 `r2` 时读，桶名，线上是 `harvester-media`。

只给 collector：
- `MEDIA_R2_WRITE_ACCESS_KEY_ID`、`MEDIA_R2_WRITE_SECRET_ACCESS_KEY`：只在 `r2` 时读，这个桶的对象读写 token。
- `MEDIA_TMP_DIR`：下载用的临时目录，默认 `/tmp/harvester-media`，启动时清空。

只给 api：
- `MEDIA_R2_READ_ACCESS_KEY_ID`、`MEDIA_R2_READ_SECRET_ACCESS_KEY`：只在 `r2` 时读，这个桶的只读 token。

`MEDIA_STORAGE=r2` 时，对应进程缺任何一项就启动报错退出；`local` 时不要求任何 R2 配置（AC-20）。密钥用 `SecretStr`，不进日志。本地 `.env.example` 写 `MEDIA_STORAGE=local`。

**上线前要准备的**（你在 Cloudflare 里做）：建私有桶 `harvester-media`，不加生命周期规则；建两个只作用于这个桶的 API token（对象读写、对象只读）；四个密钥写进服务器 `.env`，副本存密码管理器。

### Critical test scenarios

- Happy path：一条带 PNG 附件的新消息入库，`media` 行出现并在几秒内变成 `stored`，R2（测试里是 `MemoryStore`）里有原图和缩略图，`GET /api/media/{id}?variant=thumb` 跳到缩略图签名链接，频道页收到 `message_changed` 后显示图片，verifies **AC-1**、**AC-5**、**AC-14**、**AC-15**、**AC-16**
- 去重和目录：同一频道同一个月两条消息附件内容相同，只有一个 `stored_file`，已用容量只算一次；不同频道或跨月的相同内容各有一个 `stored_file`；R2 键形如 `123/2026-11/<哈希>.png`，缩略图在 `123/2026-11/thumbs/` 下，verifies **AC-4**
- 地址：embed 里 `cdn.discordapp.com/attachments/...` 建行，`https://example.com/a.png`、`http://cdn.discordapp.com/...`、`cdn.discordapp.com.evil.com`、`media.discordapp.net/external/...` 都不建行；下载得到 302 记 `gone` 且没有第二个请求，verifies **AC-2**、**AC-3**
- 上限：声明 60 MB 的附件直接 `too_large`；未知大小的 embed 图流到 50 MB 时中止并记下 `aborted_at_bytes`；调高到 60 MB 后重新排队，调到 40 MB 不会；已经满了时不下载也不换链接；没满时和已存内容相同的文件直接 `stored`，不判 `no_space`；系统状态 `media.storage_full` 为真、图标栏有红点，调高总量后恢复，verifies **AC-6**、**AC-7**、**AC-8**、**AC-9**
- 失败：CDN 连续超时，`attempts` 依次 1 到 4、`next_attempt_at` 按 1、5、30、120 分钟，第 5 次 `failed`；刚换过链接后 404 记 `gone`，403 按重试计数；1 分钟内 11 个不同的行收到 403 时暂停 30 分钟，`media_paused_until` 有值；手动重试回到 `pending`；`too_large` 调重试接口返回 409，verifies **AC-10**、**AC-17**
- 换链接：过期链接先设 `needs_refresh`，不被下载领取；换到后再下，`attempts` 不变；60 个过期链接分两批，间隔不少于 30 秒；一小时内换满 600 个就停下；句柄为空时不调用；2xx 里没返回的链接记 `gone`，5xx 和解析失败时整批行不变，verifies **AC-11**
- 频道开关：关掉频道后它的 `pending` 行不被领取也不换链接，`attempts` 不变，设置页显示暂停数量；重新打开后几秒内继续下载，verifies **AC-23**
- 竞态：下载进行中把那一行的 embed 换图重置，下载结束后写结果影响 0 行，行保持 `pending` 且指向新链接，verifies **AC-13**、**AC-18**
- 任务健壮：让下载循环抛一次意外异常，进程不退出，60 秒后继续；心跳停止超过 180 秒时首页显示「附件下载任务无响应」，verifies **AC-22**
- 下载原文件：`?download=1` 时图片的 `Content-Disposition` 是 `attachment` 且带 ASCII 兜底名；文件名里的回车换行被去掉，verifies **AC-14**、**AC-15**
- 补建：迁移在有旧附件和旧 embed 的库上运行，补出的行数正确，重跑迁移不重复；队列先处理 `message_id` 最大的，verifies **AC-12**
- 留痕：删除的消息和被拿掉的附件仍下载；embed 图换了地址后重置并重新下载，verifies **AC-13**
- 崩溃：领取后杀掉 collector，重启时 `claimed_at` 被清空并立即重新处理，`attempts` 不变；`MEDIA_TMP_DIR` 里预先放的文件在启动后消失；同一个文件上传两次不报错，verifies **AC-18**
- 安全：没登录访问 `/api/media/{id}` 返回 401；api 进程拿不到写 token（配置类没有这两个字段）；SVG 附件的签名链接带 `attachment`；日志里搜不到 `ex=` 和密钥，verifies **AC-14**、**AC-19**
- 本地模式和配置：`MEDIA_STORAGE=local` 时接口直接返回文件和正确的头；`MEDIA_STORAGE=r2` 缺读 token 时 api 启动失败，verifies **AC-20**
- 手动（`/check verify`，用真实小号和真实 R2）：真实附件端到端；一条几天前的旧附件换链接后下载成功；refresh-urls 的请求和响应格式符合预期；R2 签名链接上的 `ResponseContentDisposition` 和 `ResponseContentType` 生效；boto3 的校验和设置在 R2 上上传不报错；手机窄屏下的大图和视频，verifies **AC-1**、**AC-11**、**AC-14**、**AC-15**

## Build plan

按 Tracer Bullet：先打通「一个附件入库 → 下载 → 存进本地存储 → 接口 → 消息里看到图」这条最细的线，再逐段加粗下载可靠性、覆盖范围、界面和线上配置。

1. 打通细线：迁移建三张表、给 `collector_heartbeat` 加两列，并补建旧行（AC-12 的 SQL 部分）；`load_media_settings` 和 `MEDIA_STORAGE`；`harvester.media` 的 `urls.py`、`storage.py`（先做 `LocalStore` 和 `MemoryStore`）；`sync_media_for_message` 只处理 `kind = 'attachment'`；`media_worker` 的最简版本（领取、下载、算哈希、存储、写 `stored`、发 `message_changed`）；`GET /api/media/{id}`（本地模式）；`MessageOut.media` 和前端图片缩略框，satisfies **AC-1**、**AC-4**、**AC-12**、**AC-16**、**AC-20**
2. 下载可靠性：`policy.py`（退避、失败分类、准入）；`too_large`、`no_space`、`failed`、`gone`；写结果带 `claimed_at` 比较；启动清临时目录、释放领取，停止时清理；锁连接上的 `media_queue` 监听加 60 秒轮询；按 `message_id` 从新到旧；403 熔断和暂停；三个循环的异常兜底和下载任务心跳；`GatewayHandle` 接进 supervisor；`refresh_attachment_urls` 边界函数和换链接循环（`needs_refresh`、50 个一批、30 秒间隔、每小时 600 个、429 和不可用退避），satisfies **AC-6**、**AC-7**、**AC-10**、**AC-11**、**AC-12**、**AC-18**、**AC-22**、**AC-23**
3. 覆盖范围和缩略图：`MessageSnapshot.forwarded_attachments`；转发附件和 Discord 托管 embed 图建行、embed 换图重置；下载前的地址检查和禁止重定向；`thumbnail.py`；内容类型判断，satisfies **AC-2**、**AC-3**、**AC-5**、**AC-13**
4. 界面和设置：`media_settings` 读写接口和重新排队；两个重试接口；`?download=1` 和内容类型白名单；`/api/system/status` 的 `media` 块；前端大图、视频、音频、状态文字、重试按钮、转发块和 embed 接入、设置页「附件存储」区块、图标栏红点、首页「附件下载」行；OpenAPI 同步，satisfies **AC-7**、**AC-8**、**AC-9**、**AC-14**、**AC-15**、**AC-17**、**AC-21**、**AC-22**
5. 上线 R2：`R2Store`（`s3v4`、`region_name="auto"`、校验和设置、`upload_file` 分段上传、签名链接带响应头覆盖）；collector 和 api 分开的配置类；`compose.prod.yml` 按进程分发密钥；`deploy/README.md` 补「附件桶和两个 token」「附件不进 pg_dump、靠 R2 自身的持久性」；在 Cloudflare 建桶和 token，发布 `migrate api collector`，看着补下载跑起来，satisfies **AC-14**、**AC-19**、**AC-20**
6. 测试和收口：`policy.py`、`urls.py` 的纯函数单元测试；连真实 PostgreSQL 的 `db.media` 集成测试（建行、领取、状态、迁移补建）；用 `MemoryStore` 和假 CDN（`httpx.MockTransport`）的下载任务测试；接口鉴权和 Origin 测试；前端渲染测试（每种状态、大图、设置页校验）；各项检查全部通过，satisfies **AC-1** 到 **AC-23**

## Consequences

**Positive**：
- 附件不再随 Discord 链接过期而丢失，删除和编辑之后也能看到当时的文件，留痕完整。
- 文件不占服务器磁盘，也不经过 2 GB 服务器的带宽；R2 自带冗余，附件不需要另做备份，spec 0009 留下的附件备份问题就此关闭。
- R2 控制台里按频道和月份就能找到文件，以后要按频道或月份整组清理，按前缀操作即可（还要同步删数据库里的行，需要单独设计）。
- 同一频道同一个月里重复的图和 embed 图不重复占空间。
- `ingest_message` 统一建行，第 10 项历史补采不用再做附件相关的事。

**Negative / tradeoffs**：
- 多了一个外部依赖：R2 不可用时新附件暂时存不了（会重试，5 次后变 `failed`，需要你点「重试全部失败」）。
- 换链接是带小号登录信息的额外请求，补下载期间请求量会上升；节奏已经压低到每小时 600 个，但封号风险不是零。代价是库存大时补下载要很久（1 万个旧附件大约 17 小时）。
- 换过链接仍然 403 不会马上判死，而是重试 5 次，所以真正失效的文件要多花几个小时才落到 `failed`。
- R2 里的文件没有自己加密，靠私有桶和 token 权限保护；桶的 token 泄露等于附件泄露。
- 没有任何删除流程：容量只增不减，满了只能调高上限。以后要清理需要单独设计（要数引用）。
- 上线前已入库的转发附件存不回来；补下载期间队列可能很长，旧附件要过一阵才能看到。
- collector 的内存和 CPU 占用会因为下载和缩略图上升（同时 3 个下载，缩略图一次一张，最大约 100 MB 的解码内存）。
- supervisor 多了一个跨任务共享的 `GatewayHandle`，句柄的设置和清空时机写错会导致换链接用到已断开的会话（调用会返回 `Unavailable`，不会出错，只会延后）。
- 去重只在同一频道同一个月内生效，跨频道转发的同一张图会存多份，占用比全局去重多一些。
- 偏离了 spec 0002 的预留：0002 说「`attachment` 补字段」，这里改成独立的 `media` 表，因为转发附件和 embed 图没有 `attachment` 行。

**Neutral**：
- 新增 `boto3`、`mypy-boto3-s3`、`Pillow` 依赖，`httpx` 改为直接依赖。
- 每台开发机的 `.env` 要加 `MEDIA_STORAGE=local`。
- 数据库备份里有 `media` 和 `stored_file`，恢复旧备份后 R2 里会多出数据库不认识的文件，无害。
- 新增一个 PostgreSQL 通知通道 `media_queue`。

## Follow-up

- [ ] spec 0002 第 199 行「`attachment` 补字段（第 12 项）」加注：改由 spec 0011 的 `media` 和 `stored_file` 实现。
- [ ] spec 0009 的 Follow-up「附件保存上线后，备份会变大很多」标为已解决：附件在 R2 独立桶里，不进 `pg_dump`，不另做备份。
- [ ] spec 0003 里「embed 图直接加载，第 12 项附件代理时一起考虑」加注：Discord 托管的改走自己的存储，第三方的保持直接加载。
- [ ] 第一次 `/check verify` 时确认 Discord refresh-urls 接口在用户账号上的请求格式、每批上限，以及对已删除消息的附件是否还能换到新链接；和这里写的不一致时回到 `/architect` 调整 AC-11。
- [ ] 第一次 `/check verify` 时确认当前 boto3 版本在 R2 上需要 `request_checksum_calculation="when_required"` 和 `response_checksum_validation="when_required"`，不需要就去掉。
- [ ] 第一次 `/check verify` 时确认 R2 签名链接支持 `ResponseContentDisposition` 和 `ResponseContentType` 覆盖；不支持时改为上传时按 `content_type` 写元数据，并调整 AC-14 里文件名的来源。
- [ ] boto3 和 Pillow 的 Agent Skill 你选择了不装，`AGENTS.md` 的 `Declined:` 里应记一笔（`/sync` 负责）。
- [ ] 设计第 10 项「按时间段采集历史消息」时，让历史采集和本 spec 的换链接共用同一份带登录身份的请求额度（同一个小号），不要各算各的；到时可能要把 AC-11 的每小时 600 个改成从共用额度里扣。
- [ ] 以后如果要清理旧附件或看单个频道的占用，需要单独设计删除和引用计数。
