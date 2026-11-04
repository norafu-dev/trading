// 预览页用的确定性假消息（固定随机种子，每次生成的内容都一样）。只被 /dev/preview 引入，
// 生产构建里搜不到下面这个标记字符串（AC-15）。
import type {
  AttachmentView,
  EmbedView,
  ForwardedView,
  MentionResolver,
  MessageView,
  ReplyView,
} from "@/components/messages/types";

export const FAKE_DATA_MARKER = "__HARVESTER_FAKE_DATA__";

const DISCORD_EPOCH = 1_420_070_400_000n;

type FakeAuthor = Readonly<{ id: string; names: readonly string[]; avatarUrl: string | null }>;

const AUTHORS: readonly FakeAuthor[] = [
  { id: "800000000000000001", names: ["小鹿"], avatarUrl: null },
  { id: "800000000000000002", names: ["Kai"], avatarUrl: null },
  { id: "800000000000000003", names: ["阿橘🍊"], avatarUrl: null },
  { id: "800000000000000004", names: ["pixel_witch"], avatarUrl: null },
  { id: "800000000000000005", names: ["老王"], avatarUrl: null },
  { id: "800000000000000006", names: ["Mira"], avatarUrl: null },
  // 头像地址故意是坏的，用来看加载失败后回退成首字。
  {
    id: "800000000000000007",
    names: ["坏头像同学"],
    avatarUrl: "https://cdn.discordapp.com/avatars/800000000000000007/broken.webp?size=80",
  },
  { id: "800000000000000008", names: ["🎮 游戏机器人"], avatarUrl: null },
  // 同一个 webhook 换名字发言，要分成不同的组。
  { id: "800000000000000009", names: ["GitHub", "RSS 订阅", "构建通知"], avatarUrl: null },
  { id: "800000000000000010", names: [""], avatarUrl: null },
];

const USER_NAMES: ReadonlyMap<string, string> = new Map(
  AUTHORS.map((author) => [author.id, author.names[0] || "无名氏"]),
);

const CHANNEL_NAMES: ReadonlyMap<string, string> = new Map([
  ["910000000000000006", "闲聊"],
  ["910000000000000009", "程序"],
]);

export const FAKE_RESOLVER: MentionResolver = {
  user: (id) => USER_NAMES.get(id),
  channel: (id) => CHANNEL_NAMES.get(id),
};

// 回复引用的三种样子：正常、原消息已删除、原消息未采集。
function fakeReply(roll: number): ReplyView {
  if (roll < 0.01) return { missing: true };
  return {
    missing: false,
    authorName: "小鹿",
    preview: "这条是被回复的原消息",
    deleted: roll < 0.02,
  };
}

const SHORT = [
  "早上好！",
  "哈哈哈哈",
  "有人在吗",
  "+1",
  "这个我也遇到过",
  "刚更新了，大家试试",
  "晚点看",
  "好耶 🎉",
  "收到",
  "我觉得可以",
  "链接挂了？",
  "ok",
  "明天继续",
  "感谢分享！",
  "lgtm",
];

const LONG = [
  "今天把渲染管线重写了一遍，之前的版本在低端机上掉帧特别严重，主要是每帧都在重新分配缓冲区。现在改成预先分配一个环形缓冲，掉帧基本没了，内存占用也降了三分之一。下周打算把粒子系统也按这个思路改一下，有兴趣的可以一起看看。",
  "关于存档格式，我的建议是用版本号加迁移函数的方式，而不是每次改结构都写兼容代码。每个版本只负责从上一个版本迁移过来，读取的时候一路迁移到最新。这样新代码只需要认识最新格式，老存档也永远能读。",
  "有个超长的链接测试一下折行：https://example.com/some/really/long/path/that/keeps/going/and/going/without/any/spaces/at/all/to/see/how/it/wraps?query=1&another=2&yetanother=3",
  "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "第一行\n第二行\n\n空了一行之后的第四行",
];

const MARKDOWN = [
  "**粗体** *斜体* __下划线__ ~~删除线~~ `行内代码`",
  "剧透警告：||主角最后其实是反派||",
  "<@800000000000000002> 你看下这个，顺便叫一下 <@800000000000000099>",
  "@everyone 今晚八点开会，@here 在线的先进语音",
  "去 <#910000000000000009> 讨论，或者 <#999999999999999999>",
  "活动时间 <t:1793430000:F>，还有 <t:1793430000:R>",
  "<:pepe_happy:1000000000000000001> <a:wave:1000000000000000002> 表情测试",
  "> 引用别人说的话\n> 第二行引用\n然后是我的回复",
  "# 大标题\n## 二级标题\n### 三级标题\n正文",
  "- 第一项\n- 第二项\n  - 嵌套项\n1. 有序一\n2. 有序二",
  "[官方文档](https://docs.godotengine.org) 和 https://github.com",
  "钓鱼测试：[https://bank.example.com](https://evil.example.net/login)",
  "危险链接：[点我](javascript:alert(1))",
  "-# 这是一行小字",
  "身份组提醒 <@&123456789012345678>",
];

const CODE = [
  '```rust\nfn main() {\n    println!("hello");\n}\n```',
  '```gdscript\nfunc _ready():\n\tprint("ready")\n```',
  "```\n纯文本代码块，没有语言名\n```",
];

const FILES: readonly (readonly [string, string | null, number])[] = [
  ["screenshot.png", "image/png", 1_572_864],
  ["notes.txt", "text/plain", 1023],
  ["build-log.zip", "application/zip", 52_428_800],
  ["设计稿-v3.fig", null, 1024],
];

const EMBEDS: readonly EmbedView[] = [
  {
    type: "rich",
    title: "Godot 4.4 发布",
    description: "这一版带来了 **更快的** 着色器编译和全新的 `TileMapLayer`。",
    url: "https://godotengine.org/article",
    color: 0x478cbf,
    timestamp: "2026-10-01T08:00:00.000Z",
    author: { name: "Godot Engine", url: "https://godotengine.org" },
    fields: [
      { name: "版本", value: "4.4.0", inline: true },
      { name: "平台", value: "Windows / macOS / Linux", inline: true },
      { name: "大小", value: "68 MB", inline: true },
      { name: "更新说明", value: "修复了 200 多个问题，详见链接。", inline: false },
    ],
    footerText: "godotengine.org",
    imageUrl: "https://picsum.photos/seed/harvester/800/450",
    thumbnailUrl: "https://picsum.photos/seed/thumb/160/160",
    imageMedia: null,
    thumbnailMedia: null,
  },
  {
    type: "link",
    title: "没有颜色的 embed",
    description: "色带用边框色。",
    url: null,
    color: null,
    timestamp: null,
    author: null,
    fields: [],
    footerText: null,
    imageUrl: null,
    thumbnailUrl: null,
    imageMedia: null,
    thumbnailMedia: null,
  },
];

// mulberry32：小而确定的伪随机数，同一个种子永远得到同一串数。
function createRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4_294_967_296;
  };
}

function pick<T>(random: () => number, items: readonly T[]): T {
  const item = items[Math.floor(random() * items.length)];
  if (item === undefined) throw new Error("pick from an empty list");
  return item;
}

function snowflake(ms: number, sequence: number): string {
  return (((BigInt(ms) - DISCORD_EPOCH) << 22n) | BigInt(sequence & 0xfff)).toString();
}

function contentFor(random: () => number): string {
  const roll = random();
  if (roll < 0.5) return pick(random, SHORT);
  if (roll < 0.65) return pick(random, LONG);
  if (roll < 0.9) return pick(random, MARKDOWN);
  return pick(random, CODE);
}

function attachmentsFor(random: () => number, id: string): readonly AttachmentView[] {
  if (random() >= 0.06) return [];
  const count = 1 + Math.floor(random() * 2);
  return Array.from({ length: count }, (_, index) => {
    const [filename, contentType, sizeBytes] = pick(random, FILES);
    return { id: `${id}${index}`, filename, contentType, sizeBytes, media: null };
  });
}

function forwardedFor(random: () => number, id: string): ForwardedView | null {
  if (random() >= 0.03) return null;
  return {
    messageId: `${id}9`,
    channelId: "910000000000000009",
    guildId: "900000000000000001",
    content: "被转发的原文：**周五** 发版，记得提前测一下",
    embeds: random() < 0.5 ? [pick(random, EMBEDS)] : [],
    attachments: random() < 0.5 ? [{ filename: "changelog.md", media: null }] : [],
  };
}

// 时间间隔：多数几秒到几分钟，偶尔隔得久（另起一组），极少数隔好几个小时（跨天）。
function gapMs(random: () => number): number {
  const roll = random();
  if (roll < 0.85) return 5_000 + random() * 180_000;
  if (roll < 0.99) return 480_000 + random() * 1_920_000;
  return 6 * 3_600_000 + random() * 24 * 3_600_000;
}

// 从 `endMs` 往前生成 `count` 条消息，返回按时间从旧到新排好的列表。
export function generateMessages(count: number, endMs: number, seed: number): MessageView[] {
  const random = createRandom(seed);
  const newestFirst: MessageView[] = [];
  let time = endMs;
  let author = pick(random, AUTHORS);
  let name = author.names[0] ?? "";

  for (let index = 0; index < count; index += 1) {
    time -= Math.round(gapMs(random));
    if (random() > 0.6) {
      author = pick(random, AUTHORS);
      name = pick(random, author.names);
    }
    const id = snowflake(time, index);
    const createdAt = new Date(time).toISOString();
    const forwarded = forwardedFor(random, id);
    newestFirst.push({
      id,
      authorId: author.id,
      authorName: name,
      authorAvatarUrl: author.avatarUrl,
      createdAt,
      content: forwarded ? "" : contentFor(random),
      embeds: random() < 0.05 ? [pick(random, EMBEDS)] : [],
      forwarded,
      attachments: attachmentsFor(random, id),
      editedAt: random() < 0.08 ? new Date(time + 90_000).toISOString() : null,
      replyTo: random() < 0.06 ? fakeReply(random()) : null,
      deleted: random() < 0.03,
    });
  }
  return newestFirst.reverse();
}

export function generateOlderBatch(
  oldest: MessageView,
  count: number,
  seed: number,
): MessageView[] {
  return generateMessages(count, Date.parse(oldest.createdAt) - 1000, seed);
}

export function generateNewMessage(newest: MessageView, atMs: number, seed: number): MessageView {
  const time = Math.max(atMs, Date.parse(newest.createdAt) + 1000);
  const [message] = generateMessages(1, time + 6000, seed);
  if (!message) throw new Error("generator returned nothing");
  return { ...message, id: snowflake(time, seed), createdAt: new Date(time).toISOString() };
}

// 「消息内容样例」：每种语法、embed、转发、附件、编辑、删除各一条，方便逐项检查样式。
export function sampleMessages(endMs: number): MessageView[] {
  const bodies = [...MARKDOWN, ...CODE, LONG[2] ?? "", LONG[4] ?? ""];
  const base = (index: number, content: string): MessageView => {
    const time = endMs - (bodies.length + 6 - index) * 60_000;
    const author = AUTHORS[index % 4] ?? AUTHORS[0];
    return {
      id: snowflake(time, index),
      authorId: author?.id ?? "0",
      authorName: author?.names[0] ?? "",
      authorAvatarUrl: null,
      createdAt: new Date(time).toISOString(),
      content,
      embeds: [],
      forwarded: null,
      attachments: [],
      editedAt: null,
      replyTo: null,
      deleted: false,
    };
  };
  const plain = bodies.map((body, index) => base(index, body));
  const n = bodies.length;
  const [richEmbed, plainEmbed] = EMBEDS;
  return [
    ...plain,
    { ...base(n, "看看这个更新"), embeds: richEmbed ? [richEmbed] : [] },
    { ...base(n + 1, ""), embeds: plainEmbed ? [plainEmbed] : [] },
    {
      ...base(n + 2, ""),
      forwarded: {
        messageId: "1",
        channelId: "910000000000000009",
        guildId: null,
        content: "被转发的原文：**周五** 发版",
        embeds: [],
        attachments: [{ filename: "changelog.md", media: null }],
      },
    },
    {
      ...base(n + 3, "附件大小：1023 B、1.0 KB、1.5 MB"),
      attachments: [
        {
          id: "a1",
          filename: "notes.txt",
          contentType: "text/plain",
          sizeBytes: 1023,
          media: null,
        },
        { id: "a2", filename: "设计稿-v3.fig", contentType: null, sizeBytes: 1024, media: null },
        {
          id: "a3",
          filename: "screenshot.png",
          contentType: "image/png",
          sizeBytes: 1_572_864,
          media: null,
        },
      ],
    },
    { ...base(n + 4, "这条被编辑过"), editedAt: new Date(endMs - 30_000).toISOString() },
    { ...base(n + 5, "这条被删除了，但内容还在"), deleted: true },
  ];
}
