# Scope: Discord 频道采集器

一个只给你自己用的 Discord 频道信息采集工具。selfbot 用你的账号（建议用小号）在云服务器上 24 小时在线，你在 dashboard 里勾选要采集的频道，实时看新消息，也能按指定时间段把历史消息抓回来。

**Build approach:** Tracer Bullet（先打通一条从 Discord 到存储再到 dashboard 的真实链路，之后每个切片加粗其中一段，始终端到端可用）。
**Workflow:** Alpha（`/develop` 之后跑 `/check verify`）。这是项目默认的严谨程度。遇到真正需要做决定的功能，推荐先走 `/architect`，如果你已经知道怎么做，可以跳过。任何功能都可以带自己的标签（例如 `· Beta`）来多做或少做。

_这些都是让构建保持有序的建议，不是硬性要求。不合适的就跳过：如果你已经知道某个功能怎么做，直接用_ `/develop`_，跳过_ `/architect`_。功能什么时候算_ `done`_，由你决定。_

> **风险提示：** selfbot 违反 Discord 服务条款，账号可能被封。建议用专门的小号，并把限速和"像正常人一样"的请求节奏当成正式需求（见第 6 和第 10 项）。(basis: Discord 服务条款禁止自动化用户账号)

## At a glance

| #   | Feature              | Phase      | Status      |
| --- | -------------------- | ---------- | ----------- |
| 1   | 技术栈与架构         | Foundation | done        |
| 2   | 编码规范与工具       | Foundation | done        |
| 3   | 数据模型             | Foundation | done        |
| 4   | Dashboard 界面基础   | Foundation | done        |
| 5   | Dashboard 访问保护   | Slice 1    | done        |
| 6   | Discord 账号连接     | Slice 1    | done |
| 7   | 频道管理             | Slice 1    | in-progress |
| 8   | 实时消息流           | Slice 1    | in-progress |
| 9   | 部署与常驻运行       | Slice 1    | in-progress |
| 10  | 按时间段采集历史消息 | Slice 2    | planned     |
| 11  | 编辑与删除留痕       | Slice 3    | planned     |
| 12  | 附件与图片保存       | Slice 4    | in-progress |
| 13  | 消息搜索与筛选       | Slice 5    | planned     |
| 14  | 导出                 | Slice 6    | planned     |
| 15  | 关键词提醒           | Slice 7    | planned     |
| 16  | 睡眠登录保护和日志文件 | Slice 1    | in-progress |
| 17  | 数据库版频道未读数   | Slice 1    | in-progress |

## Foundations

### 1. 技术栈与架构 · Beta · done

决定整体技术栈（selfbot 连接方式、后端、实时推送到浏览器的方式、存储、部署形态），并搭出能跑起来的空项目，后面所有切片都在它上面构建。
**Done when:** 技术栈记录在 spec 里；空项目本地能启动、能通过 build；采集进程和 dashboard 的关系（同一进程还是分开）已经定下。
spec [0001](../specs/0001-stack-and-architecture/index.md) · code in `backend/`, `web/`, `deploy/`

- [x] Decide the stack (spec): `/architect 技术栈与架构`
- [x] Scaffold from the decision: `/develop 技术栈与架构`

### 2. 编码规范与工具 · done

在真实的脚手架项目上记录规范，然后装好 lint、format 和 pre-commit。
**Done when:** 根目录 `AGENTS.md` 反映真实技术栈，lint、format、pre-commit 都能干净跑通。

- [x] Capture conventions + tooling choices: `/audit`

### 3. 数据模型 · done

所有功能都依赖的核心实体：Discord 账号、服务器、频道及其采集开关、消息、消息的历史版本、附件、历史采集任务、关键词。(basis: 数据模型是最贵的返工)
**Done when:** 实体和关系能支撑后续切片（编辑留痕、历史采集去重、附件、搜索、多账号）而不需要破坏性迁移；同一条消息无论来自实时还是历史采集都只存一份。
spec [0002](../specs/0002-data-model/index.md) · code in `backend/src/harvester/db/`, `backend/migrations/`

- [x] Design it (spec): `/architect 数据模型`
- [x] Build it: `/develop 数据模型`
  - [x] 模型和第一次迁移（Slice 1 的表加 `message_revision`，约束和索引都写明名字），AC-1, AC-8, AC-10, AC-11
  - [x] 快照类型和纯函数（`snowflake_time`、embed 统一格式、编辑判断表），AC-5, AC-12, AC-13
  - [x] 写入函数（upsert、`ingest_message`、批量删除、覆盖段），AC-2 到 AC-10, AC-13
  - [x] 连真实 PostgreSQL 的集成测试，AC-1 到 AC-13
- [x] Verify it: `/check verify 数据模型`

### 4. Dashboard 界面基础 · done

dashboard 的视觉风格、布局和基础组件，让频道列表、消息流和任务页看起来一致。
**Done when:** `design.md` 覆盖字体、颜色、间距和基础组件；消息列表组件能处理长列表、空状态和加载状态。
spec [0003](../specs/0003-dashboard-ui-foundation/index.md) · code in `web/src/components/`, `web/src/lib/`, `web/DESIGN.md`

- [x] Design it (spec): `/architect Dashboard 界面基础`
- [x] Build it: `/develop Dashboard 界面基础`
  - [x] 打通细线：Tailwind v4、shadcn、两套 token、空外壳、`/dev/preview` 开关和生产构建排除，并验证 WASM markdown 解析器，AC-1, AC-9, AC-10, AC-15
  - [x] 纯函数、shadcn 组件和外壳（图标栏、频道列表、窄屏抽屉），AC-2, AC-3, AC-5, AC-6, AC-7, AC-12, AC-13
  - [x] 虚拟消息列表和假数据：贴底、回到最新、加载更早不跳动，AC-4, AC-5, AC-7, AC-8, AC-15
  - [x] 消息内容：markdown 渲染、embed、转发、附件、编辑删除标记、状态组件，AC-9, AC-10, AC-11, AC-12, AC-13
  - [x] 无障碍和对比度检查、`web/DESIGN.md`、单元和渲染测试，AC-14, AC-16, AC-17
- [x] Verify it: `/check verify Dashboard 界面基础`

## Slice 1: 核心链路（实时采集，也是 walking skeleton）

连上一个账号 → 勾选一个频道 → 新消息存进数据库并实时出现在 dashboard 上 → 部署到服务器一直跑。只做文字消息，不做历史、附件、搜索。

### 5. Dashboard 访问保护 · done

dashboard 部署在公网上，里面有你的账号 token 和采集数据，必须只有你能打开。
**Done when:** 没登录的人打不开任何页面和接口；你能登录、登出；会话会过期。
spec [0004](../specs/0004-dashboard-access-protection/index.md) · code in `backend/src/harvester/api/`, `backend/src/harvester/crypto.py`, `web/src/app/login/`, `web/src/app/setup/`, `deploy/`

- [x] Design it (spec): `/architect Dashboard 访问保护`
- [x] Build it: `/develop Dashboard 访问保护`
  - [x] 配置拆分、加密模块、统一错误格式、Caddy 加固，和四张表的迁移，AC-1, AC-16, AC-17, AC-18
  - [x] 细线：设置码命令、设置、登录、登出、`require_session` 和 Origin 检查，前端设置页、登录页、`AuthGate` 和 401 跳转，AC-1 到 AC-6, AC-8, AC-10, AC-13, AC-14
  - [x] 加粗：会话过期、TOTP 防重复、加锁串行的限速、登录记录和清理、SSE 会话检查、重置命令、安全页和登出入口，AC-5 到 AC-7, AC-9, AC-11, AC-12, AC-14, AC-15
  - [x] 后端集成测试和前端渲染测试，确认日志和响应里没有密钥，AC-1 到 AC-18
- [x] Verify it: `/check verify Dashboard 访问保护`

### 6. Discord 账号连接 · Beta · done

在 dashboard 填入账号 token，selfbot 用它登录并保持在线，断线自动重连，状态清楚可见。token 加密保存。(basis: 封号和 token 泄露是这个产品最大的风险)
**Done when:** 能添加一个账号并看到在线状态；断线后自动重连；token 失效或被封时 dashboard 明确提示；token 不以明文存储或出现在日志里；请求节奏有限速保护。
spec [0005](../specs/0005-discord-account-connection/index.md) · code in `backend/src/harvester/collector/`, `backend/src/harvester/api/discord_routes.py`, `backend/src/harvester/db/account.py`, `web/src/components/discord/`

- [x] Design it (spec): `/architect Discord 账号连接`
- [x] Build it: `/develop Discord 账号连接`
  - [x] 打通细线：迁移、添加和读取接口、设置页、监督循环隐身登录并写入身份，AC-1 到 AC-4, AC-16, AC-18
  - [x] 变更通知和操作：NOTIFY 加轮询、带版本的状态写入、换 token、启停、SSE 和侧栏指示，AC-10, AC-12, AC-13, AC-15
  - [x] 重连和额度：覆盖 `connect`、IDENTIFY 关卡、`login_paused`、心跳、覆盖段、SIGTERM，AC-5 到 AC-7, AC-14, AC-17
  - [x] 失败处理：失败分类、停止类状态、重新连接、身份不符和确认换身份，AC-8 到 AC-11
  - [x] 断网恢复调整：纯网络失败封顶 30 秒，RESUME 被拒后第一次等待封顶 30 秒，AC-5
- [x] Verify it: `/check verify Discord 账号连接`
- [x] Test it: `/test Discord 账号连接`

### 7. 频道管理 · in-progress

列出账号所在的服务器和频道，勾选开关决定采集哪些。
**Done when:** dashboard 按服务器分组显示频道；勾选或取消后几秒内生效，无需重启；看不到或无权限的频道有清楚标识。
spec [0006](../specs/0006-channel-management/index.md) · code in `backend/src/harvester/collector/`, `backend/src/harvester/db/channel_sync.py`, `backend/src/harvester/db/channels.py`, `backend/src/harvester/db/coverage_plan.py`, `backend/src/harvester/api/channel_routes.py`, `web/src/components/channels/`

- [x] Design it (spec): `/architect 频道管理`
- [x] Build it: `/develop 频道管理`
  - [x] 打通细线：迁移，`FullSync` 同步和权限计算，覆盖对齐，列表和开关接口，最小的管理页，AC-1, AC-3, AC-5 到 AC-7
  - [x] 增量同步：频道、线程、服务器和权限事件，合并窗口，线程依赖父频道，权限丢失和恢复联动，AC-2, AC-8
  - [x] 管理页加粗：频道树、搜索、无权限显示、分类批量、SSE 和轮询、乐观更新、窄屏，AC-4, AC-5, AC-9 到 AC-12, AC-14, AC-16
  - [x] 侧栏接真实数据和设置页入口，AC-13
  - [x] 测试和收口：鉴权、OpenAPI 同步、各项检查，AC-15, AC-17
- [ ] Verify it: `/check verify 频道管理`

### 8. 实时消息流 · in-progress

已勾选频道里的新消息存进数据库，并实时推送到 dashboard 显示。
**Done when:** 别人在已勾选频道发的消息几秒内出现在 dashboard 上，刷新后仍在；能按频道切换查看；未勾选频道的消息不被存。
spec [0008](../specs/0008-realtime-message-stream/index.md) · code in `backend/src/harvester/collector/`, `backend/src/harvester/db/messages.py`, `backend/src/harvester/api/message_routes.py`, `web/src/components/channels/`, `web/src/components/messages/`

- [x] Design it (spec): `/architect 实时消息流`
- [x] Build it: `/develop 实时消息流`
  - [x] 打通细线：消息入库、内存名单门槛、消息接口、频道页和增量取数，AC-1 到 AC-3, AC-13, AC-14, AC-16, AC-17
  - [x] 编辑、删除和可靠性：`message_changed`、重连和轮询、翻页、写入失败处理、会话收尾，AC-4, AC-5, AC-7, AC-8, AC-15, AC-16, AC-18, AC-19
  - [x] 服务器订阅：只订阅有开启频道的服务器、「加载线程」，AC-9 到 AC-12
  - [x] 阅读体验：提及名字、回复引用、未读圆点、频道状态提示，AC-6, AC-20 到 AC-23
  - [x] 测试和收口：鉴权、OpenAPI 同步、各项检查，AC-24
- [ ] Verify it: `/check verify 实时消息流`

### 9. 部署与常驻运行 · in-progress

部署到云服务器，进程崩溃或服务器重启后自动恢复，并能看到它是否还活着。
**Done when:** 服务器重启后采集自动恢复；dashboard 能看到采集进程最近一次心跳时间；有备份数据的办法；上线后跑完 spec [0004 的 verify.md](../specs/0004-dashboard-access-protection/verify.md) 里没勾的 4 个线上步骤。
spec [0009](../specs/0009-deploy-and-keep-running/index.md) · code in `deploy/`, `backend/src/harvester/db/system.py`, `backend/src/harvester/api/system_routes.py`, `web/src/components/system/`

- [x] Design it (spec): `/architect 部署与常驻运行`
- [ ] Build it: `/develop 部署与常驻运行`
  - [x] 打通心跳细线：两张表的迁移、collector 进程心跳、`collector_stale` 取或、`/api/system/status`、首页和设置页显示，AC-3, AC-4, AC-16
  - [ ] 上线服务器：`bootstrap.sh`、systemd 模板、`restore.sh`、`deploy.sh` 加锁、Release 手动入口，按手册迁移并第一次发布，AC-1, AC-2, AC-10 到 AC-13
  - [ ] 备份：`backup.sh`、R2 和 `backup.env`、首页「备份」行和图标栏红点，AC-5 到 AC-8
  - [ ] 恢复演练和收尾：Mac 上演练恢复、手册补全、spec 0004 的 4 个线上步骤，AC-9, AC-13 到 AC-15
  - [x] 测试：`harvester.db.system` 和接口的集成测试、前端渲染测试、脚本语法检查，AC-3, AC-4, AC-6, AC-7, AC-16
- [ ] Verify it: `/check verify 部署与常驻运行`

### 16. 睡眠登录保护和日志文件 · Beta · in-progress

本地 Mac 合盖和后台唤醒时不再为全新登录消耗额度，collector 和 api 的日志可以存到文件，事后能查到断线和登录的原因。(from spec 0007)
**Done when:** 合盖过夜后，后台唤醒期间没有发出全新登录；打开电脑后能自动恢复；配置 `LOG_DIR` 后能在轮转的日志文件里查到每次全新登录的原因；线上行为不变。
spec [0007](../specs/0007-sleep-login-guard-log-files/index.md) · code in `backend/src/harvester/collector/wake_guard.py`, `backend/src/harvester/logging.py`

- [x] Design it (spec): `/architect 睡眠登录保护和日志文件`
- [x] Build it: `/develop 睡眠登录保护和日志文件`
  - [x] 日志文件和诊断日志：`LOG_DIR`、轮转、权限、启动失败和登录原因，AC-7 到 AC-11
  - [x] 睡眠保护：`wake_guard.py`、IDENTIFY 前等待，RESUME 不受影响，AC-1 到 AC-6
- [ ] Verify it: `/check verify 睡眠登录保护和日志文件`
- [ ] Test it: `/test 睡眠登录保护和日志文件`

### 17. 数据库版频道未读数 · done

频道的未读状态改成存在数据库里的已读位置，侧栏显示每个频道的未读条数（满 100 显示 99+），刷新和断线重连都不丢。取代 spec 0008 的 AC-22。(from spec 0008)
**Done when:** 刷新页面后未读数保持；断线期间到达的消息在重连后出现在侧栏；打开频道后徽标消失；开启采集的新频道只从开启那刻起算未读。
spec [0010](../specs/0010-database-unread-counts.md) · code in `backend/src/harvester/db/`, `backend/src/harvester/api/message_routes.py`, `web/src/components/channels/`, `web/src/components/shell/`

- [x] Design it (spec): `/architect 数据库版频道未读数`
- [x] Build it: `/develop 数据库版频道未读数`
  - [x] 表和未读接口：`channel_read_state` 迁移回填、开启采集建行、`GET /unread`，AC-1 到 AC-3
  - [x] 前端接上未读接口：徽标和 99+、服务器圆点、删除内存逻辑，AC-5, AC-9
  - [x] 标已读：`POST .../read`、频道页上报和可见性处理，AC-4, AC-6, AC-8
  - [x] 刷新时机和收口：事件合并、重连、轮询、OpenAPI 同步、0008 的 AC-22 标注，AC-7, AC-9
- [x] Verify it: `/check verify 数据库版频道未读数`

## Slice 2: 历史采集

### 10. 按时间段采集历史消息 · needs a decision · Beta

选频道和起止时间，后台把这段时间的消息抓回来，和实时采集的数据合在一起。也用来补齐断线期间漏掉的消息。
**Done when:** 能为一个或多个频道建立指定时间段的采集任务；dashboard 显示进度、可暂停或取消；中断后能从断点继续；和实时消息不重复；请求节奏受限速保护。

- [ ] Design it (spec): `/architect 按时间段采集历史消息`

## Slice 3: 编辑与删除留痕

### 11. 编辑与删除留痕

消息被编辑时保留每个历史版本，被删除时保留原文并打上已删除标记。
**Done when:** dashboard 上被编辑的消息能查看历史版本；被删除的消息仍可见且明显标记为已删除，带删除时间。

- [ ] Build it: `/develop 编辑与删除留痕`

## Slice 4: 附件保存

### 12. 附件与图片保存 · in-progress

把消息里的图片和文件下载到自己的存储里，因为 Discord 的附件链接会过期。(basis: Discord CDN 链接带过期签名)
**Done when:** 新消息的附件自动下载保存；dashboard 里图片能预览、文件能下载，不依赖 Discord 的原链接；有单个文件大小上限和总容量的设置。
spec [0011](../specs/0011-attachment-media-storage/index.md)

- [x] Design it (spec): `/architect 附件与图片保存`
- [ ] Build it: `/develop 附件与图片保存`
  - [x] 打通细线：三张表的迁移和补建、本地存储、附件建行、最简下载任务、`GET /api/media/{id}`、消息里显示图片，AC-1, AC-4, AC-12, AC-16, AC-20
  - [ ] 下载可靠性：准入和退避、领取校验、403 熔断、异常兜底和心跳、`GatewayHandle`、换链接循环（每小时 600 个）、频道关闭时暂停，AC-6, AC-7, AC-10, AC-11, AC-18, AC-22, AC-23
  - [ ] 覆盖范围：转发附件、Discord 托管的 embed 图、换图重置、地址检查、缩略图，AC-2, AC-3, AC-5, AC-13
  - [ ] 界面和设置：设置和重试接口、大图和播放器、状态文字、设置页「附件存储」、图标栏红点、首页「附件下载」，AC-7 到 AC-9, AC-14, AC-15, AC-17, AC-21, AC-22
  - [ ] 上线 R2 和收口：`R2Store`、按进程分发密钥、建桶和 token、部署手册、测试和各项检查，AC-14, AC-19, AC-20, AC-1 到 AC-23
- [ ] Verify it: `/check verify 附件与图片保存`

## Slice 5: 搜索与筛选

### 13. 消息搜索与筛选 · needs a decision

在已采集的消息里按关键词、频道、作者、时间范围查找。
**Done when:** 关键词搜索在大量消息里也很快返回；能叠加频道、作者、时间筛选；结果能跳到上下文。

- [ ] Design it (spec): `/architect 消息搜索与筛选`

## Slice 6: 导出

### 14. 导出

把当前筛选结果导出成文件。
**Done when:** 能把筛选后的消息导出为 CSV 和 JSON；大量数据导出不会卡死页面；导出内容包含编辑和删除标记。

- [ ] Build it: `/develop 导出`

## Slice 7: 关键词提醒

### 15. 关键词提醒

设置关键词，新消息命中时在 dashboard 里提醒你。
**Done when:** 能增删关键词；命中的消息在消息流里高亮，并出现在提醒列表里，可标记已读。

- [ ] Build it: `/develop 关键词提醒`

## Deferred

这一轮不做，留在这里让计划保持诚实。

- **多账号**：在 dashboard 管理多个账号，分摊采集压力（数据模型已预留）· needs a decision
- **外部推送**：关键词提醒推送到手机或聊天工具 · needs a decision
- **数据统计**：频道活跃度、发言人排行等图表
- **AI 分析采集的信息**：作为独立的 `analyzer` 进程，用 pgvector 做向量检索（架构已预留）· needs a decision · from spec 0001
- **向交易所下单**：作为独立的 `trader` 进程；涉及真钱，要先设计密钥存放、最小权限、幂等和审计 · needs a decision · from spec 0001
- **身份组名字**：采集服务器的身份组，让消息里的 @身份组 显示真名（新表 `role`）· from spec 0003
- **查看已关闭频道的消息**：关掉开关的频道从侧栏消失后，在管理页提供入口查看已存的消息 · from spec 0008
- **新线程提示**：醒目标出新出现的线程和论坛帖子，并能一键开启某频道下的全部线程 · from spec 0006

## Legend

**决策框。** 每个功能都有且只有一个以 `(spec)` 结尾的子任务。它的措辞会变（通常是 `Design it (spec)`，技术栈上是 `Decide the stack (spec)`），各个 skill 靠 `(spec)` 后缀找到它。其余的框都是执行框，`/architect` 不会勾它们。

**功能生命周期**：功能推进时 scope 会更新：

| State                        | Set by                     | 功能显示                                                                                                                                        |
| ---------------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `planned` · needs a decision | `/scope`                   | 一个框：`Design it (spec): /architect <feature>`                                                                                                |
| `in-progress`（已设计）      | `/architect` 写完 spec 时  | `Design it` 已勾；链接 spec；`Build it: /develop <feature>` 加 2 到 5 个里程碑；按 tier 加收尾框（Alpha 起有 `Verify it`，Beta 起有 `Test it`） |
| `in-progress`（构建中）      | `/develop`                 | 里程碑逐个勾选；填上代码位置                                                                                                                    |
| `in-progress`（已验证）      | `/check verify`            | `Build it` 和里程碑已勾；`Verify it` 已勾                                                                                                       |
| `done`                       | 你决定的时候；`/sync` 对账 | Alpha 默认在 `/check verify` 之后可以算 done；Beta 在 `/test` 之后                                                                              |

- **下一步** = 第一个没勾的框。
- **needs a decision** = 先跑 `/architect`；否则直接 `/develop`（规范与工具用 `/audit`）。spec 写完后这个标签就去掉。
- **原子构建任务写在 spec 的** `## Build plan` **里，不在这里**：scope 只放里程碑汇总。
- **Status**：`planned` → `in-progress` → `done`，另外有 `existing`（工作流之前就有）和 `dropped`（移出范围，保留历史）。
- **Workflow tier 标签**（例如 `· Beta`）只改变那一个功能的严谨程度；没标签就继承项目默认的 Alpha。
- **Workflow**：Prototype = `/develop` 之后什么都不跑；Alpha = `/check verify`；Beta = `/check verify` 再 `/test`；GA = 再加全新模型 `/check review` 和 `/document`。
- **Pointer line**（`spec <n> · code in <path>`）：spec 链接由 `/architect` 加，代码位置由 `/develop` 加。

## References

**Project sources**

- 你在本次 `/scope` 中的回答：单人自用、先一个账号（以后可能多个）、云服务器 24 小时运行、浏览勾选频道、保留编辑和删除原文、提醒先只在 dashboard、核心先上其余排后。

**Practices & standards**

- Foundations before features：先打好技术栈、数据模型和界面基础，再做功能切片。
- Vertical slices ship real value early（Tracer Bullet）：第一个切片就端到端可用，后面逐段加粗。
- 数据模型是最贵的返工：所以单独成项，并预留多账号和去重。
- Discord 服务条款禁止自动化用户账号：所以限速和小号是正式需求，账号连接和历史采集提到 Beta。
- Discord 附件 CDN 链接带过期签名：所以附件要下载到自己的存储，而且越早做越少丢失。
