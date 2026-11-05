---

name: harvester-discord-wise
source: spec 0003（结构学 Discord，视觉学 Wise）
character: "左边服务器图标栏加频道列表、右边按人分组的消息流，读起来像 Discord；亮绿点缀、深森林绿文字、药丸形按钮、大圆角、平面无阴影，看起来像 Wise。安静、干净，信息密度适中，留白克制。"
tokens: "色值、字号、圆角都在 web/src/app/globals.css，这里只写名字和用途，不复制数值"
contrast: "pnpm check:contrast 两套主题 50 组全部通过：正文对背景 19.2:1 浅 / 15.3:1 深；主按钮文字 9.5:1；muted 文字最低 5.9:1；焦点环最低 3.4:1"
---

# Harvester 设计规范

这份文档是 dashboard 的美术方向和用法约定。决定来自 [spec 0003](../docs/specs/0003-dashboard-ui-foundation/index.md)；所有具体数值写在 [`src/app/globals.css`](src/app/globals.css)，改样子先改那里。

所有组件都可以在 `pnpm dev` 下打开 http://localhost:3000/dev/preview 查看（生产构建里没有这个页面）。

## Build mandate

每个页面都按完整的产品界面交付：有清楚的层次、真实的中文文案、所有状态（加载、空、错误）都有对应界面，不出现孤零零浮在空白页上的表单或控件。页面放进 `AppShell` 里，主区域用顶栏加内容的结构。

## 主题

- 浅色和深色两套，完全由系统设置（`prefers-color-scheme`）决定，不需要 JS，没有切换按钮，首次打开不闪烁。
- Tailwind 的 `dark:` 变体指向同一个媒体查询。一般不需要写 `dark:`，用 token 类就会自动跟着变。
- 浅色值在 `:root`，深色值在 `@media (prefers-color-scheme: dark) { :root { … } }`。

## 颜色 token

Tailwind 自带的调色板已经清空，只有下面这些颜色能生成类（`bg-*`、`text-*`、`border-*` 等），写 `text-green-500` 不会有任何效果。

| Token                                         | 类名示例                             | 用在哪                                                   |
| --------------------------------------------- | ------------------------------------ | -------------------------------------------------------- |
| `background` / `foreground`                   | `bg-background` `text-foreground`    | 主区域底色和正文                                         |
| `muted` / `muted-foreground`                  | `bg-muted` `text-muted-foreground`   | 频道列表底色、代码块底色；次要文字（时间、分类名、说明） |
| `card` / `card-foreground`                    | `bg-card`                            | 卡片、embed                                              |
| `popover` / `popover-foreground`              | `bg-popover`                         | 弹出层、toast、浮动提示条                                |
| `primary` / `primary-foreground`              | `bg-primary text-primary-foreground` | 主按钮、选中指示条、在线状态                             |
| `secondary` / `secondary-foreground`          | `bg-secondary`                       | 次按钮、附件标签、骨架                                   |
| `accent` / `accent-foreground`                | `bg-accent`                          | 悬停行、选中的频道                                       |
| `destructive` / `destructive-foreground`      | `bg-destructive`                     | 「已删除」标签、危险按钮、错误图标                       |
| `border` / `input`                            | `border-border`                      | 分隔线、卡片边框；输入框边框                             |
| `ring`                                        | 全局 `:focus-visible`                | 焦点环                                                   |
| `rail` / `rail-foreground`                    | `bg-rail`                            | 服务器图标栏                                             |
| `mention`                                     | `bg-mention`                         | 提及的高亮底色（半透明）                                 |
| `highlight`                                   | `bg-highlight`                       | 搜索命中词的底色（半透明黄），和提及的绿色区分           |
| `link`                                        | `text-link`                          | 消息和 embed 里的链接文字                                |
| `warning`                                     | `bg-warning`                         | 以后的提醒类状态（暂未使用）                             |
| `avatar-1` … `avatar-6` / `avatar-foreground` | `bg-avatar-3`                        | 没有头像时的首字底色，由 `fallbackColorFor(id)` 选       |

两条铁律：

- **亮绿 `primary` 只能当底色**，上面配 `primary-foreground`（森林绿）。它当文字色放在白底上对比度不够。
- 浅色主题的 `ring` 用的是中绿，不是 spec 表里的森林绿：森林绿和图标栏同色，焦点环会看不见。

## 字体和字号

- 正文：Inter（`next/font` 构建时下载并自托管），中文回退到苹方、冬青黑体、微软雅黑、Noto Sans CJK。
- 等宽：`font-mono`（系统等宽字体）。
- 字号表映射到 Tailwind 的标准名，其他默认字号已清空（写 `text-lg` 不会生效）。用标准名是为了让 `cn()` 正确合并类名。

| 类名                        | 字号 / 行高              | 用途                                             |
| --------------------------- | ------------------------ | ------------------------------------------------ |
| `text-3xl` + `font-bold`    | 28px / 1.2，字距 -0.01em | 页面标题                                         |
| `text-xl` + `font-semibold` | 20px / 1.3               | 区块标题、空状态标题                             |
| `text-base`                 | 15px / 1.5               | 消息正文；作者名再加 `font-semibold`             |
| `text-sm` + `font-medium`   | 14px / 1.4               | 界面文字（按钮、频道名），也是 `body` 的默认字号 |
| `text-xs` + `font-medium`   | 12px / 1.4               | 小字（时间、分类名、标签）                       |

## 间距、尺寸和圆角

- 间距用 Tailwind 默认的 4px 刻度，用 `gap-*`，不用 `space-x-*` / `space-y-*`。
- 消息：组与组之间 16px，同组相邻消息 2px 左右，左右内边距 16px，头像 40px，头像和文字之间 16px。
- 外壳：图标栏 72px，频道列表 240px，主区域顶栏 48px，断点 `md`（768px）。

| 圆角            | 值                 | 用在哪                                              |
| --------------- | ------------------ | --------------------------------------------------- |
| `rounded-full`  | 9999px             | 按钮、徽章、附件标签（药丸形）、头像                |
| `rounded-lg`    | 16px（`--radius`） | 卡片、弹窗、embed、抽屉、服务器图标（悬停和选中时） |
| `rounded-input` | 10px               | 输入框                                              |
| `rounded-md`    | 8px                | 频道列表的行、代码块                                |
| `rounded-sm`    | 6px                | 行内代码、提及、时间戳                              |

## 阴影和动效

- 基本不用阴影，靠边框分层（Wise 的平面风格）。只有弹出层、抽屉和浮在列表上的按钮用一层轻阴影。
- 过渡 150ms `ease-out`（Tailwind 默认的 `transition` 已改成这个值）。
- 系统开了「减少动态效果」时，`globals.css` 把所有过渡和动画压到接近 0，也关掉平滑滚动。

## 图标

`lucide-react`。图标栏里 20px，行内 16px，`strokeWidth={1.75}`。按钮里的图标加 `data-icon="inline-start"`，不加尺寸类。装饰性图标加 `aria-hidden`；只有图标的按钮必须有 `aria-label`。

## 组件

### shadcn 组件（`src/components/ui/`）

生成后已按 token 调过，当自己的代码维护：

- `Button`：药丸形。`default`（亮绿主按钮）、`secondary`、`outline`、`ghost`、`destructive`（实心红）、`link`（`text-link`）；尺寸 `xs` `sm` `default` `lg` 和对应的 `icon-*`。不要在调用处覆盖颜色，需要新样子就加 variant。
- `Badge`：药丸形。`destructive` 是实心红，用作「已删除」标签。
- `Avatar`：一定配 `AvatarFallback`（图片加载失败会自动切换）。
- `Sheet`（窄屏抽屉）、`Tooltip`、`Skeleton`、`ScrollArea`、`Separator`、`Collapsible`、`Empty`、`Spinner`、`Alert`、`Input`（高 40px，`rounded-input`）、`Label`、`Table`。
- `Toaster`（sonner）挂在 `Providers` 里，右下角弹出，`theme="system"`。临时操作的结果用 `toast.success(…)` / `toast.error(…)`。

### 本项目组件

| 组件                            | 位置                         | 用法                                                                                       |
| ------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------ |
| `AppShell`                      | `components/shell/`          | 所有 dashboard 页面的外壳，`(dashboard)/layout.tsx` 里已经包好。传入服务器、频道和采集状态 |
| `ServerRail` / `ChannelSidebar` | `components/shell/`          | 外壳的两层侧栏；频道分组由纯函数 `buildChannelTree` 生成                                   |
| `channelHref(id)`               | `components/shell/routes.ts` | 频道页地址 `/channels?id=<id>`，不要手拼                                                   |
| `MessageList`                   | `components/messages/`       | 虚拟滚动的消息列表。接口见 `types.ts` 的 `MessageListProps`；切换频道时用 `key` 重新挂载   |
| `EmptyState`                    | `components/states/`         | 所有空状态（「从左边选一个频道」「还没有开启采集的频道」）                                 |
| `ErrorBlock`                    | `components/states/`         | 首次加载失败时占据整个区域，错误由 `toUiError` 转换                                        |
| `ListSkeleton`                  | `components/states/`         | 消息列表加载中的 8 行骨架                                                                  |
| `MessageContent`                | `lib/discord-markdown/`      | 把 Discord markdown 渲染成 React 元素                                                      |

## Markdown 节点样式表

| 节点                                            | 样式                                                                                                      |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 段落                                            | 每行一个块；空行是一个空白行高                                                                            |
| 粗体 / 斜体 / 下划线 / 删除线                   | `font-semibold` / `<em>` / `<u>`（下划线偏移 2px）/ `<s>`                                                 |
| 标题 h1 / h2 / h3                               | `text-xl font-bold` / `text-base font-bold` / `text-sm font-bold`，上方留 4px                             |
| 小字 `-#`                                       | `text-xs text-muted-foreground`                                                                           |
| 列表                                            | 有序 `list-decimal`、无序 `list-disc`，左缩进 24px，可嵌套                                                |
| 引用块                                          | 左边 4px `border` 色竖条，左内边距 12px                                                                   |
| 行内代码                                        | `bg-muted`、`rounded-sm`、等宽 `text-sm`                                                                  |
| 代码块                                          | `bg-muted` 加边框、`rounded-md`、内边距 12px、最高 400px（超出在块内滚动），语言名在右上角小字            |
| 剧透                                            | 默认用 `foreground` 色整块遮住，点击或回车、空格后变成 `secondary` 底色显示；拆到多段的同一个剧透一起显示 |
| 提及（@用户、@身份组、@everyone、@here、#频道） | `bg-mention`、`rounded-sm`、`font-medium`；查不到显示「@未知用户」「#未知频道」                           |
| 链接                                            | `text-link`，悬停下划线，新标签打开，`title` 显示真实地址；文字像网址但域名不同时，后面小字提示真实域名   |
| 自定义表情                                      | 1.375em 见方的图片，加载失败显示 `:名字:`                                                                 |
| 时间戳 `<t:…>`                                  | `bg-muted rounded-sm`，悬停显示完整时间                                                                   |

## 规则

- 颜色、圆角、字号只用 token 类；Discord 给的 embed 色带是数据，用行内 `style`，这是唯一的例外。
- 条件类名用 `cn()`，不要手写模板字符串拼接。
- 消息内容只经由 React 元素渲染，从不用 `dangerouslySetInnerHTML`（Biome 会报错）。
- 所有链接和外部图片地址先过 `safeUrl`，只放行 http 和 https。
- 时间只经过 `lib/format/time.ts`（`createTimeFormat` 或 `format*` 函数），不直接用 `toLocaleString()`。
- 接口错误只在一处转换：`toUiError(error)`，然后交给 `ErrorBlock` 或 toast。
- 消息和频道的 ID 一直是字符串，不转成 `number`。
- 可点的东西都要能用 Tab 聚焦、用回车或空格触发；焦点环由全局 `:focus-visible` 统一提供，不要写 `outline-none`。
- 测量高度的容器（虚拟列表的每一行）里不要用外边距做间距，外边距会折叠出去，测出来的行高不对。用内边距。

## 不该做的事

- 写死颜色：`#fff`、`text-green-500`、`bg-[#9FE870]`。
- 亮绿当文字色，或在亮绿上放白字。
- 给卡片加阴影来分层。
- 每个组件自己判断 `response.status` 拼错误文案。
- 在消息列表外面再包一个会滚动的容器，或者给列表里的行加外边距。
- 让页面文件以 `.dev.tsx` 结尾（那是预览页专用的扩展名，生产构建会忽略它）。
