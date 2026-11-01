# Verify: 频道管理 · spec 0006 · updated 2026-11-02
_Steps derived from spec 0006 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

## 真实小号（只能手动）
- [ ] 全新登录后打开 `/settings/channels` → 10 秒内出现小号所在的服务器和频道，全程没有额外的 Discord 请求 → AC-1
- [ ] 记录 `collector.full_sync` 到写库完成的真实耗时，以及大服务器里的活跃线程和论坛帖子能拿到多少 → AC-1
- [ ] 确认 `guild.me` 和 `permissions_for` 在 `chunk_guilds_at_startup=False` 下可用；分别看一个读得了和一个读不了的频道，`can_read` 都对 → AC-3
- [ ] 确认 Discord 是否把读不了的频道发给用户账号（决定「缺席算无权限」是否合理，以及 AC-5 的措辞） → AC-5
- [ ] 在测试服务器里新建、改名、移动、删除频道 → 10 秒内页面跟着变，删除后的频道消息还在 → AC-2
- [ ] 创建、归档、取消归档、删除线程 → 10 秒内反映到库里。取消归档最要紧：它靠包装 `THREAD_UPDATE` 解析器接住，库本身不派发事件 → AC-2
- [ ] 把小号移出一个私密线程 → 覆盖段关闭；之后任意频道事件触发同步，`can_read` 仍然是假（事件名是 `thread_remove`） → AC-8
- [ ] 改一个影响小号权限的角色或频道覆盖 → 10 秒内 `can_read` 变化，覆盖段关闭；改回去后重新打开，开关全程不变 → AC-2, AC-3, AC-8
- [ ] 让小号退出一个服务器 → 服务器变成「已退出」，消息还在；重新加入后恢复 active → AC-2

## 页面
- [ ] 打开一个可读频道的开关 → 5 秒内 `channel_coverage` 多一段开着的实时段，没有重启任何进程 → AC-6, AC-7
- [ ] 关掉 → 5 秒内这一段关闭，`collect_enabled` 为假，`collector_account_id` 不变 → AC-6, AC-7
- [ ] 打开一个论坛帖子的开关 → 也开出覆盖段（论坛频道现在有访问行） → AC-7
- [ ] 看树的结构：未分类在最上，分类下是频道，线程缩进挂在父频道下，论坛和媒体频道没有开关；网址带 `?guild=<id>` → AC-4
- [ ] 无权限频道默认隐藏，工具栏写明数量；打开「显示无权限频道」后带锁图标和「无权限」，开关禁用 → AC-5
- [ ] 已开启的频道失去权限 → 始终显示警示标签「已失去权限，采集已暂停」，开关仍可关 → AC-5, AC-8
- [ ] 点分类的「全部开启」和「全部关闭」→ toast 写明改变和跳过的数量，线程不受影响 → AC-9
- [ ] 搜索框输入大小写不同的名字 → 匹配项和它的分类、父频道保留，全程没有发请求；无匹配时显示空状态 → AC-10
- [ ] 「上次同步于」显示真实时间；从未同步时显示「还没有同步过」 → AC-11
- [ ] 开两个标签页，在一个里切开关 → 另一个 5 秒内更新；断开网络让请求失败 → 开关回滚并有 toast → AC-12
- [ ] 开启一个频道 → 5 秒内出现在侧栏；全部关闭后侧栏空状态有去 `/settings/channels` 的按钮；账号页有「管理采集频道」链接 → AC-13
- [ ] 停用账号 → 页面照常显示，有离线提示，开关能保存；重新启用上线后才开覆盖段 → AC-7, AC-14
- [ ] 删掉账号或用没有账号的库打开 → 页面引导到 `/settings/discord` → AC-14
- [ ] 换身份确认后 → `channels_synced_at` 为空，开关保留；同步前可以打开开关，同步后才决定是否开段 → AC-14
- [ ] 只用键盘操作开关，读屏名称是「采集 #频道名」；窄屏先显示服务器列表，选中后显示频道树和「返回」 → AC-16

## 值的来源（spec 的 Value sourcing 表）
- [ ] `can_read`：分别让小号缺「查看频道」或缺「读取消息历史」→ 都是假 → AC-3
- [ ] 频道的 `parent_id`、`position`、`type`、`name`：和 Discord 客户端里看到的一致；把频道移到别的分类后跟着变 → AC-2
- [ ] `channels_synced_at`：内容没变的增量写入也更新；换身份后不会被在路上的旧写入写回来 → AC-11
- [ ] 服务器是否 `left`：启动时暂时不可用的服务器不会被判成已退出 → AC-2
- [ ] 线程 `archived`、`deleted`：父频道被删时线程一起变 `deleted`，分类被删时子频道不变 → AC-2
- [ ] 访问行的 `account_id` 和开关写入的 `collector_account_id`：都是库里唯一账号的 id → AC-6
- [ ] 能否打开：挑不可采集、已删除、已退出、无权限的频道直接调接口 → 分别返回对应的 409 错误码；不存在的频道返回 404 → AC-6
- [ ] 应当开着的集合：父频道失去权限或被删时，线程的覆盖段关闭 → AC-8
- [ ] 现在开着的集合：手动造一段负责账号不符的开放段 → 下次对齐把它关掉 → AC-7
- [ ] 覆盖段的时间：开始和结束都是对齐执行那一刻；`reconnecting` 期间不对齐 → AC-7
- [ ] 「已隐藏 N」：等于可采集、读不了、没开启的频道数；打开显示开关后是 0 → AC-5
- [ ] 「已开启 n」：包含失去权限但仍开启的频道 → AC-4, AC-5
- [ ] 批量结果：`changed` 和 `skipped_no_access` 和库里实际变化一致 → AC-9

## Commands
- [ ] `cd backend && uv run pytest` → 全部通过（包括没登录 401、错误 Origin 被拒绝、只有 `discord_client.py` 导入 `discord`） → AC-15, AC-17
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright` → 干净 → AC-17
- [ ] `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build` → 干净（`setup-wizard` 有一个测试在全量并行时偶尔超时，单独跑能过） → AC-17
- [ ] `uv run python -m harvester.api.export_openapi && cd ../web && pnpm gen:api`，再 `git diff` → `openapi.json` 和 `schema.d.ts` 没有变化 → AC-17

## Acceptance-criteria coverage
- AC-1 真实小号 1、2 · AC-2 真实小号 5 到 9、值的来源 · AC-3 真实小号 3、8 · AC-4 页面第 4 步 · AC-5 真实小号 4、页面第 5、6 步 · AC-6 页面第 1、2 步、值的来源 · AC-7 页面第 1 到 3 步 · AC-8 真实小号 7、8、页面第 6 步 · AC-9 页面第 7 步 · AC-10 页面第 8 步 · AC-11 页面第 9 步 · AC-12 页面第 10 步 · AC-13 页面第 11 步 · AC-14 页面第 12 到 14 步 · AC-15 Commands 第 1 步 · AC-16 页面第 15 步 · AC-17 Commands 全部
