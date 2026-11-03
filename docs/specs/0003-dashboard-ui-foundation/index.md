# 0003. Dashboard 界面基础

**Date**: 2026-10-31
**Status**: Accepted

## Summary

这份 spec 定下 dashboard 的样子和骨架：结构学 Discord（左边服务器图标栏加频道列表，右边是按人分组的消息流），视觉学 Wise（亮绿点缀、深森林绿文字、药丸形按钮、大圆角、留白干净），浅色和深色两套主题跟随系统。组件用 Tailwind CSS v4 加 shadcn/ui（底层 Base UI），长消息列表用 TanStack Virtual 做虚拟滚动（只渲染屏幕上看得见的那几十条），Discord markdown 用 Discord 官方的解析库。这一项只做组件和规范，用一个开发专用的预览页加 5 万条假消息来验证；接真实接口、实时推送和翻历史留给第 8 项。

## Requirements

**User stories**:
- 作为唯一的使用者，我想在左边点一个服务器、再点一个频道，右边就看到这个频道的消息，排版像 Discord 一样好读，这样我能快速扫完采集到的内容。
- 作为唯一的使用者，我想让界面在白天和晚上都舒服（跟随系统的浅色和深色），手机上也能凑合看。
- 作为之后写第 5 到 15 项的人，我想要一套写好的颜色、字号、间距和基础组件，这样每个新页面看起来都一致，不用每次重新决定样子。

**Acceptance criteria**:
- **AC-1**: 浅色和深色两套主题由系统设置决定（`prefers-color-scheme`），首次打开不闪烁。所有颜色、圆角、字体都定义成 `web/src/app/globals.css` 里的 CSS 变量（下文的 token），组件里不出现写死的颜色值。
- **AC-2**: 宽度不小于 768px 时，页面是三块：72px 宽的服务器图标栏，240px 宽的频道列表，剩下的是主区域。点一个服务器图标，频道列表换成这个服务器的频道。频道列表只列出开了采集的频道（`collect_enabled = true`，状态不是 `deleted`），按分类分组，分类和频道都按 `position` 排序（没有 `position` 的排在最后，再按名字），线程缩进挂在父频道下面（父频道没开采集时，父频道以灰色、不可点的形式出现，只为挂住线程）。当前选中的服务器和频道有明显的高亮，并带 `aria-current`。
- **AC-3**: 宽度小于 768px 时，图标栏和频道列表一起收起，主区域顶栏左边出现一个菜单按钮（`aria-label="打开导航"`），顶栏中间显示当前频道名；点按钮从左边滑出抽屉（两层侧栏都在里面），选了频道后抽屉自动关上；窗口宽度跨过 768px 时抽屉也自动关上。
- **AC-4**: 消息列表在 50,000 条假消息下滚动流畅，任何时候 DOM 里带 `data-message-row` 的元素不超过 100 个（预览页实时显示这个数）。打开时停在最底部（最新的消息），等行高测量稳定后再定位一次，确保真的在底部。
- **AC-5**: 连续的消息满足以下全部条件时合并成一组：同一个 `authorId`，同一个 `authorName`（同一个 webhook 换名字发言要分开），和上一条间隔小于 7 分钟，在浏览器本地时区的同一个日历日。已删除的消息不打断分组。每组第一条显示 40px 头像、显示名和时间，后面的只显示内容，鼠标悬停时在左侧空白处显示 24 小时制的「HH:mm」。跨越本地日期时插入一条日期分隔线（例如「2026年10月30日」）。
- **AC-6**: 时间按浏览器本地时区、`zh-CN` 格式、24 小时制显示：今天的显示「14:05」，昨天的显示「昨天 14:05」，今年更早的显示「10月30日 14:05」，往年的显示「2025年10月30日 14:05」。「今天」和「昨天」按时区里的日历日判断，不按相差 24 小时算（夏令时切换那天也正确）。悬停显示完整时间「2026年10月30日 星期五 14:05:32」。
- **AC-7**: 如果新消息到来时你离底部不超过 80px，列表自动滚到底；否则位置不动，右下角出现「回到最新」按钮，上面带上这段时间新来的条数；点按钮滚到底，按钮消失。新来的条数按「末尾的 ID 比上次在底部时看到的最大 ID 更大」来数，所以加载旧消息、消息被编辑或删除都不算新消息。
- **AC-8**: 滚到离顶部不超过一屏高度（视口的 `clientHeight`）时，列表调用一次 `onReachTop`；初次定位到底部完成之前不调用；在 `isLoadingOlder` 为真期间不会重复调用；`hasOlder` 为假时不再调用。加载中时列表顶部显示一行转圈。旧消息插到顶部之后，你正在看的那条消息停在屏幕上原来的位置，不跳动。加载旧消息失败时（`olderError` 有值）顶部显示一行小字和「重试」按钮，下面已有的消息不动。
- **AC-9**: 消息内容按 Discord markdown 渲染：粗体、斜体、下划线、删除线、行内代码、代码块（带语言名）、引用块、标题、列表、普通链接、带文字的链接、剧透、@用户、@身份组、@everyone 和 @here、#频道、自定义表情、`<t:时间戳:格式>`。各节点的样式按 `web/DESIGN.md` 的节点样式表。全程不用 `dangerouslySetInnerHTML`。所有链接（正文、embed 的标题、作者、图片）都经过同一个 `safeUrl` 检查，只允许 `http` 和 `https`，其他协议当普通文字；链接在新标签打开并带 `rel="noopener noreferrer nofollow"`；带文字的链接悬停时显示真实地址，而且链接文字本身像网址、但域名和真实地址不同时，在文字后面用小字显示真实域名（防钓鱼）。剧透默认遮住，是一个 `role="button"`、带 `aria-expanded` 的元素，点击或按回车、空格后显示。@用户 显示查到的名字，查不到显示「@未知用户」；#频道 查不到显示「#未知频道」；@身份组 统一显示「@身份组」。所有提及（包括查不到名字的）、@everyone 和 @here 都用高亮底色。自定义表情显示 Discord CDN 上的图片，加载失败时显示 `:名字:`。`<t:…>` 按下文的格式字母对照表显示，悬停显示完整时间。
- **AC-10**: 解析器就绪之前，消息列表显示骨架（不先显示纯文本再切换，避免行高跳动）；3 秒内还没就绪或加载失败，就降级为纯文本显示（保留换行），之后不再切换。解析单条消息出错时，只有这条显示原始文字。页面不白屏，也不影响其他消息。加载失败只在控制台记一次错误。
- **AC-11**: 有 embed 的消息在正文下面显示 embed 卡片：左边一条色带（用 embed 的 `color`，没有就用边框色），依次是作者、标题（有 `url` 就是链接）、描述（按 markdown 渲染，不截断）、字段（`inline` 的字段宽屏一行最多三个，窄屏一行一个）、大图、右上角缩略图、页脚文字和时间。大图放在最宽 400px 的 16:9 固定框里，用 `object-contain` 显示；缩略图是 80px 的方框。图片尺寸固定，加载前后行高不变。转发的消息显示一个带「转发」标签的引用块，里面是被转发的文字、embed 和附件文件名。附件显示成小标签：回形针图标、文件名、大小（按 1024 进位，显示成 B、KB、MB、GB，保留一位小数）。编辑过的消息（`version > 1`）在内容后面加小字「（已编辑）」，悬停显示编辑时间。已删除的消息（`deleted_at` 有值）照常显示内容，但整体变淡，并加一个红色的「已删除」标签。
- **AC-12**: 头像用 Discord CDN 的图片；没有 `avatar_hash` 或图片加载失败时，显示名字的第一个字（按字形取，emoji 也算一个字；名字为空显示「?」），底色从 `--avatar-1` 到 `--avatar-6` 中按用户 ID 选一个，固定不变。webhook 和机器人同样处理，不使用 Discord 的默认头像。服务器图标同理（用 `icon_hash`，没有就显示服务器名第一个字）。
- **AC-13**: `MessageList` 自己负责三种状态：加载中显示 8 行消息骨架（头像圆、名字条、两行正文条）；`status` 为 `ready` 但没有消息时（以及 `status` 为 `empty` 时）显示「这个频道还没有采集到消息」；首次取数失败（`status` 为 `error`）时整个列表位置显示错误块（标题、说明、「重试」按钮，按钮调用 `onRetry`）。页面和外壳负责另外两种，用同一个 `EmptyState` 组件：没选频道显示「从左边选一个频道」；一个开了采集的频道都没有显示「还没有开启采集的频道」。保存、删除这类临时操作的结果用 toast（右下角弹出、几秒后消失的提示）。
- **AC-14**: 所有可点的东西都能用键盘 Tab 到并用回车或空格触发，焦点有清楚可见的焦点环；两套主题里，下文「对比度检查」列出的每一组前景和背景都不低于 WCAG AA（普通文字 4.5:1，焦点环和图标 3:1），由脚本检查；系统开了「减少动态效果」时关掉过渡动画和平滑滚动。
- **AC-15**: `/dev/preview` 在 `pnpm dev` 下能打开，展示所有基础组件、两套主题、全部状态和一个 5 万条假消息的列表（带「模拟新消息」和「模拟加载更早的消息」按钮，以及 DOM 消息行计数）。生产构建根本不生成这个路由：`pnpm build` 之后 `out/dev/` 不存在，产物里也搜不到假数据生成器里的标记字符串 `__HARVESTER_FAKE_DATA__`。
- **AC-16**: `web/DESIGN.md` 写好，覆盖 token、字体、间距、圆角、组件用法、markdown 节点样式表和不该做的事；`pnpm typecheck`、`pnpm build`、`biome check` 都通过。
- **AC-17**: 纯函数有单元测试，关键组件有渲染测试（见 Critical test scenarios），全部通过。

## Decision

**Chosen option**: Option 1: Tailwind CSS v4 加 shadcn/ui（Base UI），自定义 Discord 结构加 Wise 视觉的 token，TanStack Virtual 写消息列表，`@discord/markdown-wasm` 解析 markdown

在现有的 Next.js 静态导出前端里，用 shadcn/ui 生成可改的基础组件，用一套跟随系统的浅色和深色 token 定下 Wise 风格的样子，自己用 TanStack Virtual 写 Discord 式的消息列表，用 Discord 官方的 WASM 解析器加自定义渲染函数把消息变成 React 元素。

**Implementation skills**: `shadcn` (`shadcn-ui/ui`, `.claude/skills/shadcn/`) · `tailwind-4-docs` (`lombiq/tailwind-agent-skills`, `.claude/skills/tailwind-4-docs/`) · `vercel-react-best-practices` (`vercel-labs/agent-skills`, `.claude/skills/vercel-react-best-practices/`) · `tanstack-query` (`tanstack-skills/tanstack-skills`, `.claude/skills/tanstack-query/`)

有意偏离 `shadcn` skill 的两处（你已确认）：消息列表不用它推荐的 `MessageScroller`（它不做虚拟化，几万条会卡），消息行也不用 `Message` 和 `Bubble`（那是 AI 聊天的气泡样式，不是 Discord 的样子）。其余按 skill 的惯例（Base UI 用 `render` 组合，样式写在 token 和 variant 里）。

## Rationale

理由和其他选项见 [rationale.md](rationale.md)。

## Standard definition

### 设计 token

所有值写在 `web/src/app/globals.css`。浅色值放在 `:root`；深色值放在 `@media (prefers-color-scheme: dark) { :root { … } }` 里，Tailwind 的 `dark:` 变体用 `@custom-variant dark (@media (prefers-color-scheme: dark));` 指向同一个媒体查询。这样完全不需要 JS，也就不会闪烁（AC-1）。`:root` 上加 `color-scheme: light dark`，让滚动条和表单控件也跟着变。名字沿用 shadcn 的变量名，额外加几个本项目专用的。

| Token | 浅色 | 深色 | 用在哪 |
|---|---|---|---|
| `--background` | `#FFFFFF` | `#121511` | 主区域（消息流）底色 |
| `--foreground` | `#0E0F0C` | `#E8EBE6` | 正文 |
| `--muted` | `#F4F6F3` | `#1A1E18` | 频道列表底色、骨架、代码块底色 |
| `--muted-foreground` | `#5D605B` | `#A3A8A0` | 次要文字（时间、分类名、说明） |
| `--card` / `--popover` | `#FFFFFF` | `#1F241D` | 卡片、弹出层、embed 底色 |
| `--card-foreground` / `--popover-foreground` | `#0E0F0C` | `#E8EBE6` | |
| `--primary` | `#9FE870` | `#9FE870` | 主按钮、选中指示条、在线状态 |
| `--primary-foreground` | `#163300` | `#163300` | 主按钮上的文字（森林绿配亮绿） |
| `--secondary` | `#ECEFEA` | `#262B23` | 次按钮 |
| `--secondary-foreground` | `#163300` | `#E8EBE6` | |
| `--accent` | `#E2F6D5` | `#24321C` | 悬停行、选中的频道 |
| `--accent-foreground` | `#163300` | `#D7F5C2` | |
| `--destructive` | `#A8200D` | `#F2735F` | 「已删除」标签、危险按钮、错误 |
| `--destructive-foreground` | `#FFFFFF` | `#1A0500` | |
| `--border` | `#E0E3DE` | `#2C3229` | 分隔线、卡片边框 |
| `--input` | `#C9CDC6` | `#3A4136` | 输入框边框 |
| `--ring` | `#163300` | `#9FE870` | 焦点环 |
| `--rail` | `#163300` | `#0B0D0A` | 服务器图标栏底色 |
| `--rail-foreground` | `#FFFFFF` | `#E8EBE6` | 图标栏上的字和图标 |
| `--mention` | `rgb(159 232 112 / 0.28)` | `rgb(159 232 112 / 0.16)` | 提及的高亮底色 |
| `--link` | `#2F5711` | `#B7F08F` | 消息里的链接文字 |
| `--warning` | `#EDC843` | `#EDC843` | 以后的提醒类状态（本项暂不用） |
| `--avatar-1` … `--avatar-6` | `#CDEFB5` `#D6E4F0` `#F0DFC8` `#E3D7F0` `#CFE8E2` `#F2D4D0` | `#2F4A1F` `#24384A` `#4A3A24` `#3A2D4A` `#1F4440` `#4A2A26` | 没有头像时的首字底色 |
| `--avatar-foreground` | `#163300` | `#E8EBE6` | 首字颜色 |

亮绿 `#9FE870` 不能当文字色放在白底上（对比度不够），只能做底色，上面配森林绿文字。

**接进 Tailwind**：`@theme inline` 里把每个 token 映射成 Tailwind 颜色，例如 `--color-primary: var(--primary);`、`--color-rail: var(--rail);`、`--color-avatar-1: var(--avatar-1);`，表里每一行都要映射。映射之前先 `--color-*: initial;` 清掉 Tailwind 自带调色板，再补回 `--color-white: #fff;`、`--color-black: #000;`、`--color-transparent: transparent;`、`--color-current: currentColor;`（shadcn 生成的代码会用到）。shadcn 生成代码里的 `.dark` 选择器全部改成 `dark:` 变体（也就是媒体查询）；`sonner` 的 `Toaster` 设 `theme="system"`。

**对比度检查**（`web/scripts/check-contrast.ts`，两套主题都跑，`pnpm check:contrast`）：`foreground` 对 `background`、`muted`、`card`、`accent`、`mention`（叠在 `background` 上）；`muted-foreground` 对 `background`、`muted`、`card`；`primary-foreground` 对 `primary`；`secondary-foreground` 对 `secondary`；`accent-foreground` 对 `accent`；`destructive` 对 `background`；`destructive-foreground` 对 `destructive`；`link` 对 `background`、`card`；`rail-foreground` 对 `rail`；`avatar-foreground` 对六个 `avatar`；`ring` 对 `background`、`muted`、`rail`（3:1）。不达标的组合在构建前改色值。

**字体**：`next/font/google` 引入 Inter（构建时下载并自托管，运行时不请求 Google），变量名 `--font-inter`。正文字体栈：`var(--font-inter), "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`。等宽：`ui-monospace, "SF Mono", Menlo, Consolas, monospace`。

| 用途 | 字号 / 行高 | 字重 |
|---|---|---|
| 页面标题 | 28px / 1.2 | 700，字间距略收紧（`-0.01em`） |
| 区块标题 | 20px / 1.3 | 600 |
| 消息正文 | 15px / 1.5 | 400 |
| 作者名 | 15px / 1.5 | 600 |
| 界面文字（按钮、频道名） | 14px / 1.4 | 500 |
| 小字（时间、分类名、标签） | 12px / 1.4 | 500 |

**圆角**：`--radius: 16px`（卡片、弹窗、embed、抽屉）。按钮和标签全圆（`9999px`，药丸形）。输入框 `10px`。频道列表的行 `8px`。服务器图标平时是圆形，悬停和选中时变成 `16px` 圆角方形（和 Discord 一样），左边出现一条亮绿的指示条。

**间距和尺寸**：用 Tailwind 默认的 4px 刻度。舒适密度：消息组之间 16px，同组相邻消息之间 2px，消息行左右内边距 16px，头像 40px，头像和文字之间 16px。图标栏 72px，频道列表 240px，主区域顶栏 48px，断点 `md` 等于 768px。

**图标**：`lucide-react`，图标栏 20px，行内 16px，`strokeWidth={1.75}`。

**阴影和动效**：基本不用阴影，用边框区分层次（Wise 的平面风格）；只有弹出层和抽屉用一层轻阴影。过渡 150ms `ease-out`；`prefers-reduced-motion: reduce` 时关掉过渡和平滑滚动（AC-14）。

### 组件清单

**shadcn 组件**（`pnpm dlx shadcn@latest add …`，生成到 `web/src/components/ui/`）：`button`、`badge`、`avatar`、`tooltip`、`skeleton`、`scroll-area`、`separator`、`sheet`、`sonner`、`alert`、`collapsible`、`empty`、`spinner`。生成后按 token 调 variant（按钮药丸形等），之后当自己的代码维护。

**本项目组件**（按功能放在一起，全部具名导出）：

| 位置 | 内容 |
|---|---|
| `web/src/components/shell/` | `AppShell`（三块布局和窄屏抽屉）、`ServerRail`、`ChannelSidebar`、`CollectorStatusDot`、`build-channel-tree.ts`（纯函数）、`routes.ts`（`channelHref(id)` 返回 `/channels?id=<id>`） |
| `web/src/components/messages/` | `MessageList`（虚拟滚动）、`MessageRow`（`memo`）、`DayDivider`、`JumpToLatestButton`、`OlderLoader`（顶部转圈和重试）、`Embed`、`ForwardedBlock`、`AttachmentChip`、`use-message-scroll.ts`（贴底、新消息计数、顶部加载、插入后保持位置）、`build-rows.ts`（分组和日期分隔线，纯函数）、`types.ts`（下面的视图类型） |
| `web/src/components/states/` | `ListSkeleton`、`EmptyState`、`ErrorBlock` |
| `web/src/lib/discord-markdown/` | `parser.ts`（加载 WASM、`parse`，只在这一处碰这个库）、`use-markdown-parser.ts`（返回 `"pending" \| "ready" \| "failed"`，带 3 秒超时）、`renderers.tsx`（每种语法节点对应一个渲染函数）、`MessageContent.tsx` |
| `web/src/lib/format/` | `time.ts`（`formatMessageTime`、`formatHoverTime`、`formatFullTime`、`formatDayDivider`、`formatDiscordTimestamp`，都显式接收 `now` 和时区）、`bytes.ts`（`formatBytes`）、`use-now.ts`（每 60 秒更新一次的 `now`，由页面调用后传给列表） |
| `web/src/lib/safe-url.ts` | `safeUrl(raw): URL \| null`（只放行 `http:`、`https:`）、`looksLikeUrl(text)`、`hostDiffers(text, url)` |
| `web/src/lib/discord-cdn.ts` | `avatarUrl`、`guildIconUrl`、`emojiUrl`、`initialFor`、`fallbackColorFor` |
| `web/src/lib/api/errors.ts` | `UiError` 类型和 `toUiError(unknown)`，前端把接口错误变成界面状态的唯一入口 |
| `web/src/app/dev/preview/page.dev.tsx` | 预览页（文件名以 `.dev.tsx` 结尾，见下文路由）；假数据在 `web/src/dev/fake-messages.ts`，只被预览页引入 |
| `web/scripts/check-contrast.ts` | 对比度检查脚本 |

### 组件接口（这一项定死，第 8 项按它把接口数据转换进来）

```ts
// web/src/components/messages/types.ts
export type MessageView = Readonly<{
  id: string;               // Discord 雪花 ID，用字符串，因为超过 JS 安全整数
  authorId: string;
  authorName: string;       // message.author_display_name
  authorAvatarUrl: string | null; // avatarUrl(author_id, discord_user.avatar_hash)
  createdAt: string;        // ISO 8601，UTC
  content: string;
  embeds: readonly EmbedView[];
  forwarded: ForwardedView | null;
  attachments: readonly AttachmentView[]; // 只含 removed_at 为空的
  editedAt: string | null;  // version > 1 时有值（取 edited_at，没有就取最近一次修订时间）
  deleted: boolean;         // deleted_at 不为空
}>;

export type MentionResolver = Readonly<{
  user: (id: string) => string | undefined;
  channel: (id: string) => string | undefined;
}>;

export type EmbedView = Readonly<{
  type: string | null;
  title: string | null;
  description: string | null;
  url: string | null;
  color: number | null;           // 0xRRGGBB 整数
  timestamp: string | null;       // ISO 8601
  author: Readonly<{ name: string; url: string | null }> | null;
  fields: readonly Readonly<{ name: string; value: string; inline: boolean }>[];
  footerText: string | null;
  imageUrl: string | null;
  thumbnailUrl: string | null;
}>;

export type ForwardedView = Readonly<{
  messageId: string;
  channelId: string;
  guildId: string | null;
  content: string;
  embeds: readonly EmbedView[];
  attachmentNames: readonly string[];
}>;

export type AttachmentView = Readonly<{
  id: string;
  filename: string;
  contentType: string | null;
  sizeBytes: number;              // 附件大小在安全整数范围内，可以用 number
}>;

export type MessageListProps = Readonly<{
  messages: readonly MessageView[];   // 按 id 从旧到新
  resolver: MentionResolver;
  now: Date;                          // 页面用 useNow() 每 60 秒更新后传入
  timeZone: string;                   // Intl.DateTimeFormat().resolvedOptions().timeZone
  hasOlder: boolean;
  isLoadingOlder: boolean;
  olderError?: UiError;               // 加载旧消息失败，只在顶部显示
  onReachTop: () => void;
  status: "loading" | "ready" | "empty" | "error"; // 只描述首次加载
  error?: UiError;                    // status 为 error 时必有
  onRetry?: () => void;               // 首次加载和旧消息加载共用
}>;
```

字段和 spec 0002 的 `EmbedData`、`ForwardedData`、`attachment` 一一对应，改成 camelCase 和字符串 ID；`EmbedData` 里没写入的键在视图里是 `null`，`fields` 没有就是空数组。

### 虚拟列表的参数（`MessageList` 和 `use-message-scroll`）

- 日期分隔线和消息放在同一个 `useVirtualizer` 里，行由 `buildRows` 生成：`{ kind: "day", key: "day:2026-10-30" }` 或 `{ kind: "message", key: <消息ID>, groupStart: boolean }`。`getItemKey` 返回行的 `key`，所以插入旧消息后同一条消息的测量缓存仍然有效。`buildRows` 用 `useMemo` 按 `messages`、`timeZone` 缓存，全量重算（5 万条在几毫秒内，不做增量）。
- `estimateSize`：组首消息 72px，后续消息 28px，日期分隔线 40px；`overscan: 10`；每行用 `measureElement` 实测，行元素带 `data-index` 和 `data-message-row`（分隔线不带后者）。
- 初次定位：`scrollToIndex(最后一行, { align: "end" })`，在下一帧测量完成后再执行一次。完成之后才允许 `onReachTop`。
- 贴底判断：`scrollHeight - scrollTop - clientHeight <= 80`。贴底时 `messages` 末尾变化或任一行高变化，都滚到底。
- 插入旧消息后保持位置：调用 `onReachTop` 前记下第一条可见行的 `key` 和它离视口顶部的距离；`messages` 前面变长后，找到同一个 `key` 的新下标，测量完成后用 `scrollToOffset(新行起点 - 原距离)` 恢复。不依赖 `shouldAdjustScrollPositionOnItemSizeChange` 的默认行为。
- 新消息计数：记住最近一次贴底时看到的最大消息 ID（按 `BigInt` 比较），不贴底时，末尾 ID 大于它的条数就是计数。

### `<t:时间戳:格式>` 对照表

| 格式字母 | 示例输出 |
|---|---|
| `t` | 14:05 |
| `T` | 14:05:32 |
| `d` | 2026/10/30 |
| `D` | 2026年10月30日 |
| `f`（也是省略格式时的默认） | 2026年10月30日 14:05 |
| `F` | 2026年10月30日 星期五 14:05 |
| `R` | 用 `Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" })` 和 `now` 算：「3 分钟前」「昨天」「2 天后」，随 `now` 每分钟更新 |

时间戳不是合法整数或格式字母不认识时，显示原文。

```ts
// web/src/lib/api/errors.ts
export type UiError = Readonly<{
  kind: "network" | "unauthorized" | "not_found" | "server" | "unknown";
  title: string;     // 例如「连不上服务器」
  detail: string;    // 给人看的一句说明
  retryable: boolean;
}>;
```

### 规则（每个前端文件都遵守）

**Canonical pattern**:
```tsx
// 颜色、圆角只走 token；变体写在组件里，不在调用处拼
<Button variant="secondary" size="sm">重试</Button>
<div className="rounded-lg border border-border bg-card text-card-foreground">…</div>

// 接口错误只在一处转换
const query = useQuery(...);
if (query.isError) return <ErrorBlock error={toUiError(query.error)} onRetry={query.refetch} />;
```

**Replaces**:
- 写死的颜色（`#fff`、`text-green-500`、`bg-[#9FE870]`）
- 每个组件自己判断 `response.status` 拼错误文案
- 用 `dangerouslySetInnerHTML` 或第三方 HTML 字符串渲染消息
- 消息时间用 `toLocaleString()` 直接格式化（不经过 `lib/format/time.ts`）

**Enforcement**:
- Biome：确认 `biome.json` 里 `security/noDangerouslySetInnerHtml` 是 `error`，拦住第三条。
- `pnpm build` 时 Tailwind 只认 token 生成的颜色类：`@theme` 里用 `--color-*: initial;` 清掉 Tailwind 自带调色板，只注册上表的 token，这样 `text-green-500` 根本不会生成样式，写了也立刻看得出来。任意值写法（`bg-[#…]`）靠代码审查拦。
- TypeScript：`MessageListProps`、`UiError` 等类型让调用方无法绕开。

**Rollout**: 新代码立即生效。现有的 `src/app/page.tsx` 和 `system-status.tsx` 在本项里改成用新组件和 token。

**Exceptions**: Discord 给的 embed 色带颜色（`embed.color`）是数据，不是样式，用行内 `style` 设置，不算违反。

## Feature design

**Data model sketch**: 不改表结构。只读 spec 0002 已有的 `guild`、`channel`、`discord_user`、`message`、`attachment`（第 8 项接接口后）。本项只用前端假数据。

**State transitions**: 无。

**API surface**: 本项不加接口。接口由第 7 项（服务器和频道列表）和第 8 项（消息列表、SSE）提供，它们负责把数据转成上面的视图类型。

**页面和路由**:
- `web/src/app/(dashboard)/layout.tsx`：路由组，所有 dashboard 页面包在 `AppShell` 里。第 5 项的登录页放在路由组外面，不带侧栏。
- `/`（`(dashboard)/page.tsx`）：主区域显示「从左边选一个频道」和现有的系统状态。第 7 项接上之前，侧栏拿到的是空列表，显示「还没有开启采集的频道」。
- `/channels?id=<频道ID>`：频道页的网址格式在这里定死，`channelHref` 生成它；页面本身由第 8 项建。读查询参数要用 `useSearchParams`，而且只把依赖它的部分（侧栏的选中高亮、频道页主体）包在 `Suspense` 里，fallback 是同样结构的骨架，避免整个外壳在首屏闪空（静态导出的要求）。
- 选中的服务器：`AppShell` 里的 React state。有选中频道时，跟着频道的 `guild_id`；没有时默认第一个服务器（按名字排序）。不进网址，刷新后回到默认，这是可以接受的。
- `/dev/preview`：页面文件叫 `page.dev.tsx`。`next.config.ts` 里写 `pageExtensions: isDev ? ["tsx", "ts", "dev.tsx"] : ["tsx", "ts"]`（`isDev` 由 `process.env.NODE_ENV !== "production"` 得出），所以生产构建根本不把它当成页面，`out/dev/` 不会出现，它引入的假数据也不进产物（AC-15）。注意：这样一来，项目里普通页面文件不能以 `.dev.tsx` 结尾。
- 预览页的模拟参数：初始 50,000 条；「模拟新消息」每次在末尾追加 1 条；「模拟加载更早的消息」每次在前面插入 500 条，延迟 300ms，插入 20 次之后 `hasOlder` 变成 `false`；另有一个开关模拟加载旧消息失败。假数据生成器是确定性的（固定随机种子），文件里带标记字符串 `__HARVESTER_FAKE_DATA__`。

**Value sourcing**:
| Action | Value produced / displayed | Source |
|---|---|---|
| 图标栏 | 服务器名、图标 | `guild.name`、`guildIconUrl(guild.id, guild.icon_hash)`：`https://cdn.discordapp.com/icons/{id}/{hash}.{webp\|gif}?size=96`，`hash` 以 `a_` 开头用 `gif` |
| 图标栏、消息行 | 没图片时的首字和底色 | `initialFor(name)`（用 `Intl.Segmenter` 取第一个字形，emoji 也算一个；空名字返回「?」）、`fallbackColorFor(id)`（`BigInt(id) % 6n` 选 `--avatar-1` 到 `--avatar-6`） |
| 图标栏 | collector 状态点 | 组件 prop `status: "online" \| "offline" \| "unknown"`，第 9 项提供心跳；本项一律传 `"unknown"`（灰色） |
| 频道列表 | 分组、顺序、缩进 | `buildChannelTree(channels)` 纯函数，输入 `channel` 的 `id`、`guild_id`、`parent_id`、`type`、`name`、`position`、`collect_enabled`、`status` |
| 频道列表 | 选中项 | 网址里的 `id` |
| 消息行 | 作者名 | `message.author_display_name` |
| 消息行 | 头像 | `avatarUrl(author_id, discord_user.avatar_hash)`：`https://cdn.discordapp.com/avatars/{id}/{hash}.{webp\|gif}?size=80`；`hash` 为空时用首字 |
| 消息行 | 显示时间、悬停时间、日期分隔线 | `message.created_at` 加上 `now` 和 `timeZone` 两个 prop |
| 消息行 | 是否另起一组 | `buildRows`：比较相邻两条的 `authorId`、`createdAt` 差值（小于 7 分钟）和本地日期 |
| 消息行 | 「（已编辑）」和悬停时间 | `editedAt`，来自 `message.edited_at`；`version > 1` 但 `edited_at` 为空时取最新一条 `message_revision.replaced_at`（第 8 项转换时处理） |
| 消息行 | 「已删除」 | `message.deleted_at` 不为空 |
| 消息内容 | @用户 名字 | `resolver.user(id)`，第 8 项用 `discord_user.global_name`，没有就 `username`；查不到显示「@未知用户」 |
| 消息内容 | #频道 名字 | `resolver.channel(id)`，来自 `channel.name`；查不到显示「#未知频道」 |
| 消息内容 | @身份组 | 固定文字「@身份组」（库里不存身份组，见 Follow-up） |
| 消息内容 | 自定义表情图片 | `emojiUrl(id, animated)`：`https://cdn.discordapp.com/emojis/{id}.{webp\|gif}?size=48`，`animated` 来自 `<a:name:id>` 写法 |
| 消息内容 | `<t:…>` 显示 | 时间戳和格式字母都在原文里，按上文对照表，用 `formatDiscordTimestamp` 加 `now` 和 `timeZone` |
| 消息内容、embed | 链接是否可点、是否显示真实域名 | `safeUrl(href)`；`looksLikeUrl(文字)` 并且 `hostDiffers(文字, href)` 时显示域名 |
| 消息行 | 悬停时分 | `formatHoverTime(createdAt, timeZone)`，`HH:mm` |
| embed | 色带颜色 | `embed.color`（整数）转成 `#RRGGBB`；空的用 `--border` |
| 附件 | 文件名、大小 | `attachment.filename`、`formatBytes(attachment.size_bytes)` |
| 转发块 | 附件文件名 | `forwarded.attachment_names` |
| 「回到最新」按钮 | 新消息条数 | `use-message-scroll`：末尾 ID 大于最近一次贴底时看到的最大 ID 的条数，回到底部清零 |
| 错误块 | 标题、说明、能否重试 | `toUiError(error)` |

**Key invariants**:
- 消息和频道 ID 在前端始终是字符串，不转成 `number`（雪花 ID 超过 2^53，会被悄悄改错）。
- `MessageList` 的 `messages` 按 `id` 升序，由调用方保证；组件不排序。虚拟列表每行的 key 是消息 ID（日期分隔线用 `day:<日期>`）。
- 格式化函数和 `buildRows` 是纯函数：时间和时区都从参数传入，不在函数里调用 `new Date()`。
- `@discord/markdown-wasm` 只在 `lib/discord-markdown/parser.ts` 里被引入；其他地方只通过它导出的带类型函数使用（项目规则：缺类型或不稳定的第三方库只在边界包一层）。
- 唯一允许的模块级可变状态：`parser.ts` 里缓存的 WASM 初始化 promise（只赋值一次，保证整个页面只初始化一次）。这是对「模块级变量只能是常量」规则的有意例外，在文件里用注释写明。
- 渲染函数表必须覆盖解析器类型里的每一种节点（用 TypeScript 的穷举检查，`satisfies Record<NodeType, Renderer>`）；运行时遇到不认识的节点，按它的文字内容渲染。

**Security model**:
- 消息内容全部是不受信任的数据，只经由 React 元素渲染，从不当 HTML 插入（AC-9）。
- 链接只放行 `http:` 和 `https:`，用 `new URL()` 解析后检查协议；`javascript:`、`data:` 等一律当文字。
- 头像、服务器图标、表情直接从 `cdn.discordapp.com` 加载（你已确认，代价是 Discord 能看到你的 IP）。embed 里的大图和缩略图可能来自任意网站，同样直接加载，加 `referrerPolicy="no-referrer"` 和 `loading="lazy"`，不做代理（第 12 项附件代理时一起考虑是否改走自己的接口）。
- 预览页和假数据不进生产包。
- 本项不涉及登录；访问保护由第 5 项负责。

**Configuration required**: 无新环境变量。

**边界情况**:
- 解析器 WASM 不在 `Providers` 里全站加载，只在用到 `MessageList` 的页面（频道页和预览页）由 `use-markdown-parser` 懒加载；`pending` 时列表显示骨架，`ready` 后渲染，3 秒超时或失败就是 `failed`，全部消息用纯文本显示（AC-10）。WASM 文件要随静态产物输出到 `out/`，Caddy 默认就用 `application/wasm` 提供 `.wasm`，不需要改 Caddyfile（构建计划第 2 步确认）。
- 很长的消息：长单词和长链接用 `overflow-wrap: anywhere` 折行；代码块最高 400px，超出在块内滚动。
- 图片加载失败：头像和服务器图标回退首字；表情回退 `:名字:`；embed 图片直接隐藏，不留破图标。
- 行高变化：embed 图片用固定尺寸的框，加载前后行高不变；其他变化（字体加载、窗口变宽变窄）由 `measureElement` 重新测量，贴底状态下高度变化后仍保持在底部。
- 时间每分钟刷新一次（页面层的 `now`），所以「今天」和「昨天」过了午夜会自己更正。

**Critical test scenarios**:
- `buildRows`：同作者 6 分 59 秒内合并，7 分钟另起一组；换作者另起一组；同 ID 换名字另起一组；中间夹一条已删除消息仍合并；跨本地午夜插入分隔线并另起一组；在 `Asia/Tokyo` 和 `America/Los_Angeles` 两个时区下结果不同；前面插入 500 条后，原有消息的行 `key` 不变，verifies **AC-5**、**AC-8**
- `formatMessageTime`：今天、昨天、今年、往年四种，加跨年的一月一日，加美国夏令时切换当天的「昨天」，verifies **AC-6**
- `formatDiscordTimestamp`：七个格式字母各一条、省略格式、`R` 的过去和未来、非法时间戳显示原文，verifies **AC-9**
- `safeUrl` 和 `hostDiffers`：`javascript:`、`data:` 返回空；`[https://a.com](https://evil.com)` 判定为域名不同，verifies **AC-9**
- 新消息计数（从 `use-message-scroll` 抽出的纯函数）：前面插入旧消息不计数，末尾追加计数，编辑已有消息不计数，verifies **AC-7**
- `initialFor`：中文名、英文名、emoji 开头、空名字，verifies **AC-12**
- `buildChannelTree`：分类排序、`position` 为空排最后、线程挂在父频道下、父频道没开采集时以占位出现、`deleted` 不出现，verifies **AC-2**
- 渲染测试：手写的语法树经过渲染函数后，`javascript:` 链接变成文字，剧透默认隐藏、点击后显示，查不到的提及显示占位，verifies **AC-9**
- 渲染测试：解析器抛错时显示原文并保留换行，verifies **AC-10**
- 渲染测试：`MessageRow` 的已编辑、已删除、附件大小（1023 B、1.0 KB、1.5 MB）、embed 色带为空时用边框色，verifies **AC-11**
- 渲染测试：`avatarUrl` 对 `a_` 开头的 hash 用 `gif`；hash 为空时 `MessageRow` 显示首字，verifies **AC-12**
- 渲染测试：`MessageList` 的加载中、空、错误三种状态各渲染对应的界面，错误块的重试按钮调用 `onRetry`；`olderError` 时顶部出现重试，下面消息仍在，verifies **AC-8**、**AC-13**
- 渲染测试：解析器 `pending` 时显示骨架，`failed` 时显示纯文本，verifies **AC-10**
- 解析器集成测试（这个测试文件用 `// @vitest-environment node` 加载 WASM，其他渲染测试用 jsdom）：一条包含全部语法的样例消息解析出预期的节点类型，verifies **AC-9**
- 对比度脚本：两套主题所有组合通过，verifies **AC-14**
- 构建检查：`pnpm build` 之后 `out/dev/` 不存在，`grep -r __HARVESTER_FAKE_DATA__ out/` 无结果，verifies **AC-15**
- 预览页手动验证：5 万条滚动时 DOM 行计数不超过 100、贴底、回到最新、加载更早不跳动、窄屏抽屉、两套主题、键盘操作，verifies **AC-3**、**AC-4**、**AC-7**、**AC-8**、**AC-14**

## Build plan

按 Tracer Bullet：先打通「token 到外壳到一条消息到预览页到生产构建」这条细线，确认 Tailwind v4、shadcn、静态导出和预览页的开关都能工作，再一段一段加粗。风险最大的 WASM 解析器放在第二步尽早验证。

1. 装 Tailwind CSS v4 和 shadcn/ui（`pnpm dlx shadcn@latest init --base base`，按 Next 模板，别名对齐 `@/*`），写好 `globals.css` 的两套 token、`@theme inline` 映射、`@custom-variant dark`、清空默认调色板并补回 white、black、transparent、current，用 `next/font` 引入 Inter；建 `(dashboard)` 路由组和一个只有三块空区域的 `AppShell`；在 `next.config.ts` 加 `pageExtensions` 开关，建 `/dev/preview`（`page.dev.tsx`）显示一条纯文本 `MessageRow`；确认 `pnpm build` 通过，`out/dev/` 不存在。satisfies **AC-1**、**AC-15**
2. 验证 `@discord/markdown-wasm` 和 `@discord/markdown-react`：先看 npm 上的发布者和许可证确实可用；再确认在 Next 16 静态导出下 `.wasm` 文件进到 `out/`，浏览器里 `init()` 成功（本地经 Caddy 访问 `:8080` 也成功），Vitest node 环境也能加载。四项都满足才算通过。如果卡住超过半天，换成备选 `discord-markdown-parser`（纯 JS，同样输出语法树，也就不需要骨架闸门和超时），在本 spec 的 Decision 里记一笔。satisfies **AC-9**、**AC-10**
3. 写纯函数：`lib/format/time.ts`（含 `<t:…>` 对照表）、`lib/format/bytes.ts`、`lib/safe-url.ts`、`lib/discord-cdn.ts`、`components/messages/build-rows.ts`、新消息计数函数、`components/shell/build-channel-tree.ts`、`lib/api/errors.ts`；再写 `use-now.ts`。satisfies **AC-5**、**AC-6**、**AC-7**、**AC-9**、**AC-11**、**AC-12**、**AC-13**
4. 加 shadcn 组件（组件清单里那些），按 token 调好 `button`（药丸形）、`badge`、`avatar` 等的 variant。satisfies **AC-1**、**AC-14**
5. 做外壳：`ServerRail`、`ChannelSidebar`、`CollectorStatusDot`、窄屏 `Sheet` 抽屉、`channelHref`；预览页用假的服务器和频道数据演示。把现有首页和 `system-status.tsx` 换成新组件和 token。satisfies **AC-2**、**AC-3**
6. 做 `MessageList`：按上文「虚拟列表的参数」实现 TanStack Virtual、`use-message-scroll`（初次两次定位、贴底、新消息计数和「回到最新」、顶部触发 `onReachTop`、插入旧消息后按 key 恢复位置）、`DayDivider`、`OlderLoader`；写确定性的假数据生成器（混合长短消息、代码块、embed、转发、附件、编辑、删除、表情、提及、webhook 换名），预览页按上文的模拟参数加按钮、失败开关和 DOM 行计数。satisfies **AC-4**、**AC-5**、**AC-7**、**AC-8**、**AC-15**
7. 做 `MessageContent`：解析器边界、`use-markdown-parser` 的骨架闸门和 3 秒超时、全部节点的渲染函数（`safeUrl`、防钓鱼域名提示、可键盘操作的剧透、提及高亮、表情、`<t:…>`、代码块）、单条出错时的纯文本回退。satisfies **AC-9**、**AC-10**
8. 做 `Embed`、`ForwardedBlock`、`AttachmentChip`、已编辑和已删除标记。satisfies **AC-11**、**AC-12**
9. 做 `ListSkeleton`、`EmptyState`、`ErrorBlock`，在 `Providers` 旁边挂上 `Toaster`；预览页展示全部状态和一个 toast 按钮。satisfies **AC-13**
10. 无障碍检查：写 `scripts/check-contrast.ts` 和 `pnpm check:contrast`，按「对比度检查」列表两套主题都通过；键盘走一遍外壳和消息列表，查焦点环、`aria-current`、剧透的 `aria-expanded`、减少动态效果。satisfies **AC-14**
11. 写 `web/DESIGN.md`：token 表、字体、间距、圆角、组件清单和用法、markdown 节点样式表（标题 h1 到 h3 的字号、列表缩进、引用块左边条、行内代码和代码块底色、剧透遮罩、提及高亮）、上面的规则和不该做的事。satisfies **AC-9**、**AC-16**
12. 用 `/test` 装好 Vitest 和 Testing Library，写 Critical test scenarios 里的测试。satisfies **AC-17**

## Consequences

**Positive**:
- 第 5 到 15 项的页面直接用现成的 token、外壳和状态组件，不再各自决定样子。
- 消息列表的接口（`MessageView`、`MessageListProps`）先定死，第 8 项只需要把接口数据转进来，不会和界面互相牵扯。
- 不用 JS 切主题，没有闪烁，也没有主题状态要管理。
- 消息从不当 HTML 插入，XSS（网页里被插入恶意脚本）的路被堵死。

**Negative / tradeoffs**:
- `@discord/markdown-wasm` 还是 0.x 版本，以后升级可能有破坏性改动；它要加载 WASM，第一次打开频道时要多等一小会儿骨架。
- 预览页靠 `pageExtensions` 排除，普通页面文件从此不能以 `.dev.tsx` 结尾。
- 自己写虚拟列表的贴底、计数和插入后保持位置，大约多一两百行代码，而且这种滚动逻辑不好自动测试，只能在预览页手动验证。
- 没有手动切换主题的按钮，想在白天看深色只能改系统设置。
- 头像、表情和 embed 图片直接从 Discord 和第三方网站加载，这些网站能看到你的 IP。
- shadcn 生成的组件是复制进仓库的代码，以后 shadcn 更新不会自动同步，要手动对比。

**Neutral**:
- 前端新增依赖：`tailwindcss`、`@tailwindcss/postcss`、shadcn 生成时带进来的 Base UI 和工具库、`@tanstack/react-virtual`、`lucide-react`、`sonner`、`@discord/markdown-wasm`、`@discord/markdown-react`、`@discord/markdown-types`。
- 第 8 项的接口要把 ID 序列化成字符串，并提供提及名字的查询数据。

## Follow-up

- [ ] 第 8 项：消息接口里所有雪花 ID 以字符串返回（Pydantic 端序列化成 `str`），并返回本页消息里提到的用户和频道的名字，供 `MentionResolver` 使用。
- [ ] 第 8 项：频道页 `/channels?id=` 按本 spec 的网址格式建，接 SSE 和向上翻历史。
- [ ] 后端统一错误格式定下来后（AGENTS.md 规则里的「固定格式的 JSON 错误」），在 `toUiError` 里加上对它的解析。
- [ ] 以后考虑采集身份组名字（新表 `role`），让 @身份组 显示真名。
- [ ] 第 12 项：决定 embed 图片和头像是否改走自己的接口代理，不再让浏览器直接请求第三方。
- [ ] 根目录 `AGENTS.md` 的 `## Agent skills` 补上 `shadcn`（`shadcn-ui/ui`）和 `tailwind-4-docs`（`lombiq/tailwind-agent-skills`），`Declined` 一行补上 shadcn MCP（由 `/sync` 写）。
- [ ] `web/AGENTS.md` 或根目录 `## Rules` 补一条：前端颜色和圆角只用 `web/DESIGN.md` 的 token（由 `/sync` 写）。
- [ ] `tailwind-4-docs` skill 第一次用之前要运行它自带的 `scripts/sync_tailwind_docs.py` 同步文档快照（需要联网）。
