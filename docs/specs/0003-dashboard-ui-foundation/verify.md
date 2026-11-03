# Verify: Dashboard 界面基础 · spec 0003 · updated 2026-11-01
_Steps derived from spec 0003 acceptance criteria. `/check verify` runs these; `/test` locks the durable ones._

预览页：`cd web && pnpm dev`，打开 http://localhost:3000/dev/preview（或经本地 Caddy 的 http://localhost:8080/dev/preview）。

## UI / manual
- [x] 系统切到浅色打开预览页，再切到深色（或开发者工具里模拟 `prefers-color-scheme: dark`）→ 两套颜色都正确，刷新时没有先白后黑的闪烁 → AC-1
- [x] 在 `src/` 里搜 `#[0-9a-fA-F]{3,6}`、`bg-[#`、`text-green-` → 除 `globals.css` 和 embed 色带的行内 `style` 外没有写死的颜色 → AC-1
- [x] 窗口宽于 768px，看「外壳」一节 → 72px 图标栏 + 240px 频道列表 + 主区域；点「🎧 音乐分享」图标 → 频道列表换成「每日推荐」「黑胶交流」 → AC-2
- [x] 外壳里的「独立游戏开发」→ 分类「公告」「讨论」按 position 排序；「已删掉的频道」不出现；「没有排序位置的频道」排在讨论分类最后；「像素风格求助」挂在灰色、不可点的「美术」下面；「存档丢失了怎么办」挂在灰色的「反馈论坛」下面 → AC-2
- [x] 点「Godot 4.4 迁移」→ 它高亮，开发者工具里带 `aria-current="page"`；当前服务器图标带 `aria-current="true"`，左边有亮绿指示条 → AC-2
- [x] 窗口窄于 768px → 两层侧栏收起，顶栏左边出现菜单按钮（`aria-label="打开导航"`），中间是当前频道名 → AC-3
- [x] 点菜单按钮 → 抽屉从左边滑出，里面有图标栏和频道列表；点一个频道 → 抽屉自动关上 → AC-3
- [x] 打开抽屉后把窗口拉宽过 768px → 抽屉自动关上 → AC-3
- [x] 「消息列表」一节：打开时停在最底部，最后一条消息完整可见；上下快速滚动 → 流畅，「DOM 消息行」徽章始终不超过 100 → AC-4
- [x] 看分组：同一人 7 分钟内的连续消息只有第一条显示头像和名字；「GitHub」「RSS 订阅」「构建通知」（同一个 webhook 换名）各自另起一组；跨日期出现「2026年10月30日」样式的分隔线；悬停后续消息时左侧显示 `HH:mm` → AC-5
- [x] 时间：今天的显示 `14:05`，昨天的显示 `昨天 14:05`，更早的显示 `10月30日 14:05`；悬停时间显示 `2026年10月30日 星期五 14:05:32` → AC-6
- [x] 在底部时点「模拟新消息」→ 自动滚到底；往上滚开一点再点两次 → 位置不动，右下角出现「回到最新 2 条新消息」；点它 → 回到底部，按钮消失 → AC-7
- [x] 滚到接近顶部 → 顶部出现「正在加载更早的消息」转圈，加载完成后你正在看的那条消息停在原位置，不跳动；连续加载 20 次后徽章显示「没有更早的了」，不再触发 → AC-8
- [x] 勾选「加载更早的消息时失败」再滚到顶 → 顶部出现「连不上服务器，更早的消息没加载出来」和「重试」，下面的消息不动；取消勾选后点「重试」→ 正常加载 → AC-8
- [x] 「消息内容样例」一节：粗体、斜体、下划线、删除线、行内代码、代码块（带语言名）、引用、标题、列表、链接、剧透、@用户、@未知用户、@身份组、@everyone、@here、#频道、#未知频道、自定义表情（加载失败显示 `:名字:`）、`<t:…:F>` 和 `<t:…:R>` 都按 `web/DESIGN.md` 的样式表显示 → AC-9
- [x] 剧透默认遮住；用 Tab 聚焦后按回车或空格 → 显示；开发者工具里是 `role="button"`，`aria-expanded` 从 false 变 true → AC-9
- [x] `[点我](javascript:alert(1))` 显示成普通文字；链接在新标签打开，带 `rel="noopener noreferrer nofollow"`；悬停显示真实地址 → AC-9
- [x] 开发者工具 Network 里把 `*.wasm` 请求设为阻止，刷新 → 列表先显示骨架，3 秒内改成纯文本（保留换行），之后不再切换，控制台只有一条错误 → AC-10
- [x] embed：左边色带用 embed 颜色（「没有颜色的 embed」用边框色）；作者、标题链接、描述、三个 inline 字段一行、整行字段、16:9 大图、右上角缩略图、页脚都在；窄屏下字段一行一个 → AC-11
- [x] 转发块带「转发」标签，里面有文字和附件名；附件标签显示「1023 B」「1.0 KB」「1.5 MB」；「（已编辑）」悬停显示编辑时间；已删除的消息整体变淡并带红色「已删除」 → AC-11
- [x] 「坏头像同学」的头像回退成首字「坏」；同一个用户的底色固定；服务器图标显示服务器名第一个字（「🎧 音乐分享」显示 🎧） → AC-12
- [x] 「消息列表的三种状态」：加载中是 8 行骨架；没有消息显示「这个频道还没有采集到消息」；首次加载失败显示错误块和「重试」 → AC-13
- [x] 打开 http://localhost:3000/ → 主区域显示「从左边选一个频道」和系统状态卡片；侧栏显示「还没有开启采集的频道」 → AC-13
- [x] 「状态和提示」里点「弹出成功提示」→ 右下角出现 toast，几秒后消失 → AC-13
- [x] 只用键盘：从图标栏开始一路 Tab → 每个服务器按钮、分类按钮、频道链接、按钮、剧透都能聚焦，焦点环清楚；灰色占位父频道被跳过；回车或空格能触发 → AC-14
- [x] 系统开「减少动态效果」→ 抽屉、提示没有过渡动画 → AC-14

## Commands
- [x] `cd web && pnpm check:contrast` → 两套主题 50 组全部「通过」，退出码 0 → AC-14
- [x] `cd web && pnpm build && ls out/dev` → 提示目录不存在 → AC-15
- [x] `cd web && grep -r __HARVESTER_FAKE_DATA__ out/` → 没有结果 → AC-15
- [x] `cd web && pnpm typecheck && pnpm build && pnpm biome check .` → 全部通过 → AC-16
- [x] 打开 `web/DESIGN.md` → 有 token、字体、间距、圆角、组件用法、markdown 节点样式表、规则和不该做的事 → AC-16
- [x] `cd web && pnpm test`（`/test` 装好 Vitest 之后）→ Critical test scenarios 里的单元和渲染测试全部通过 → AC-17

## Value sourcing
- [x] 服务器图标：给 `guildIconUrl` 传 `a_` 开头的 hash → 地址以 `.gif?size=96` 结尾；普通 hash → `.webp`；hash 为空 → 显示首字 → AC-12
- [x] 首字和底色：`initialFor("🎮 游戏")` 是 🎮，`initialFor("")` 是「?」；同一个 ID 两次调用 `fallbackColorFor` 结果相同 → AC-12
- [x] collector 状态点：传 `"unknown"` → 灰色，提示「采集进程状态未知」 → AC-2
- [x] 频道列表分组和选中项：改网址里的 `?id=` → 高亮跟着变，服务器自动切到该频道所在的服务器 → AC-2
- [x] 作者名、头像：webhook 换名时显示的是每条消息自己的 `authorName` → AC-5
- [x] 时间：把系统时区分别改成 `Asia/Tokyo` 和 `America/Los_Angeles` 刷新预览页 → 时间和日期分隔线跟着时区变；美国夏令时切换当天的「昨天」仍然正确 → AC-5, AC-6
- [x] 分组：两条同作者消息相差 6 分 59 秒合并，相差 7 分钟另起一组 → AC-5
- [x] 「（已编辑）」：`editedAt` 有值才显示，悬停显示 `editedAt` 的完整时间 → AC-11
- [x] 「已删除」：`deleted` 为 true 才显示 → AC-11
- [x] @用户、#频道：resolver 查得到显示名字，查不到显示「@未知用户」「#未知频道」；@身份组固定显示「@身份组」 → AC-9
- [x] 自定义表情：`<a:wave:…>` 请求 `.gif`，`<:pepe:…>` 请求 `.webp` → AC-9
- [x] `<t:…>`：七个格式字母各试一次，非法时间戳显示原文；`R` 过一分钟后自己更新 → AC-9
- [x] 链接：`safeUrl` 拒绝 `javascript:` 和 `data:`；`hostDiffers("bank.example.com", new URL("https://evil.example.net"))` 为 true → AC-9
- [x] embed 色带：`color` 为 `0x478cbf` → 色带 `#478cbf`；为空 → 边框色 → AC-11
- [x] 附件大小：1023 → 「1023 B」，1024 → 「1.0 KB」，1572864 → 「1.5 MB」 → AC-11
- [x] 「回到最新」计数：在前面插入旧消息不计数，末尾追加计数，编辑已有消息不计数 → AC-7
- [x] 错误块：`toUiError(new ApiResponseError(503))` → 「服务器出错了」可重试；401 → 「需要重新登录」不可重试；`TypeError` → 「连不上服务器」 → AC-13

## Acceptance-criteria coverage
- AC-1 … UI 第 1、2 步 · AC-2 … UI 第 3 到 5 步、Value sourcing 第 3、4 步 · AC-3 … UI 第 6 到 8 步 · AC-4 … UI 第 9 步 · AC-5 … UI 第 10 步、Value sourcing 第 5 到 7 步 · AC-6 … UI 第 11 步、Value sourcing 第 6 步 · AC-7 … UI 第 12 步、Value sourcing「回到最新」 · AC-8 … UI 第 13、14 步 · AC-9 … UI 第 15 到 17 步、Value sourcing 提及到链接 · AC-10 … UI 第 18 步 · AC-11 … UI 第 19、20 步、Value sourcing 编辑、删除、色带、附件 · AC-12 … UI 第 21 步、Value sourcing 第 1、2 步 · AC-13 … UI 第 22 到 24 步、Value sourcing 错误块 · AC-14 … UI 第 25、26 步、Commands 第 1 步 · AC-15 … Commands 第 2、3 步 · AC-16 … Commands 第 4、5 步 · AC-17 … Commands 第 6 步（等 `/test`）
