# Verify: 实时消息流 · spec 0008 · updated 2026-11-02
_Steps derived from spec 0008 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

开始前：重启 collector 和 api，让它们跑新代码。重启 collector 会消耗一次登录额度。

## UI / manual
- [ ] 开启一个频道，用另一个账号发一条消息 → 3 秒内出现在 `/channels?id=<频道ID>`，刷新后仍在 → AC-1, AC-16, AC-17
- [ ] 查看 `collector` 日志，对比发送时间和入库时间 → 正常情况下不超过 1 秒 → AC-1
- [ ] 在没开启的频道和私信里发消息 → `message` 表没有新行 → AC-2
- [ ] 小号自己发一条消息和一条回复 → 都入库，`source` 是 `realtime` → AC-1
- [ ] 编辑一条消息 → 页面就地更新并显示「（已编辑）」，`message_revision` 有旧版本 → AC-4, AC-18
- [ ] 发一条带链接的消息，等 embed 展开 → embed 出现，`version` 不变，没有「（已编辑）」 → AC-4
- [ ] 查看日志里编辑事件解析失败的警告，确认 embed 展开时载荷是否完整 → 记下结果，问题明显时回到 spec 0008 → AC-4
- [ ] 删除一条消息，再批量删除几条 → 显示「已删除」，原文还在 → AC-4, AC-18
- [ ] 断网一小段时间后恢复（RESUME） → 期间的消息不重复，覆盖段没断 → AC-5
- [ ] 让会话走全新登录 → 覆盖段按断线时间关闭，`FullSync` 后重新打开 → AC-5
- [ ] 打开未订阅的大服务器（超过 75,000 人）里的频道 → 5 秒内开始收到消息 → AC-9, AC-10
- [ ] 查看没开启频道的小服务器是否仍推消息 → 消息不入库；记下是否有推送 → AC-2, AC-9
- [ ] 关掉一个服务器的全部频道 → 这个会话不退订，消息不入库；下一个会话不再订阅 → AC-11
- [ ] 在 `enabled_count` 为 0 的服务器点「加载线程」 → 显示「正在加载」，5 秒内新线程出现在管理页，按钮恢复 → AC-12
- [ ] 账号离线时点「加载线程」 → 显示「账号上线后加载」，上线后完成 → AC-12
- [ ] 改变小号的角色 → 记下没有 `member_updates` 时权限变化是否仍能收到 → AC-9
- [ ] 往上滚动 → 每次加载 50 条更早的消息，位置不跳 → AC-16
- [ ] 切换频道；打开 `/channels`（没有 `id`）和不存在的 ID → 列表回到最新处；显示「从左边选一个频道」；显示错误块 → AC-16
- [ ] 回复一条消息，分别让原消息存在、被删除、不在库里 → 引用行对应正常、「（已删除）」、「原消息未采集」 → AC-20
- [ ] 发 `@用户`、`#频道`、`@身份组` → 显示 `global_name` 或 `username`、频道名、固定文字 → AC-6, AC-21
- [ ] 转发一条消息 → 转发块内容正确 → AC-14
- [ ] 让别的频道来新消息 → 频道和服务器图标都有圆点，打开后清除，（AC-22 已由 spec 0010 取代，刷新后未读保持，见 spec 0010）→ AC-22
- [ ] 依次制造每种状态：频道删除、退出服务器、关闭采集、失去权限、线程归档、父频道失权、重连、离线、刚开启 → 显示对应提示，恢复后消失 → AC-23
- [ ] 查看 `/dev/preview` 和窄屏下的频道页，两套主题 → 回复行和未读圆点显示正常 → AC-20, AC-22

## Value sourcing
- [ ] 在开启前、对齐完成后各发一条消息 → 只有后者入库，存不存只看 `open_channels` → AC-3
- [ ] 改小号的服务器昵称后发消息 → `author_display_name` 是昵称；webhook 换名字发言时用这条消息上的名字 → AC-1
- [ ] 把浏览器时区改成另一个时区 → 消息时间和日期分隔线跟着变，库里的 `created_at` 不变 → AC-16
- [ ] 找一个超过 2^53 的消息 ID，看接口响应和 SSE → ID 是字符串，和库里完全一致 → AC-14, AC-15
- [ ] 点「加载线程」后查 `guild.thread_load_requested_at` → 请求时有值，订阅后为空 → AC-12

## Commands
- [ ] `cd backend && uv run pytest` → 全部通过 → AC-1 到 AC-9, AC-11 到 AC-15, AC-20, AC-23, AC-24
- [ ] `cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright` → 无错误 → AC-24
- [ ] `cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build` → 全部通过 → AC-16 到 AC-24
- [ ] `grep -rl "import discord" backend/src/harvester` → 只有 `collector/discord_client.py` → AC-24
- [ ] 跑一段时间后在日志里搜一条测试消息的正文 → 搜不到 → AC-24

## Acceptance-criteria coverage
- AC-1 到 AC-8：真实小号步骤加自动测试
- AC-9 到 AC-12：真实小号步骤（大服务器只能实测）加自动测试
- AC-13 到 AC-15：自动测试加大 ID 步骤
- AC-16 到 AC-23：页面步骤加 Vitest
- AC-24：Commands
