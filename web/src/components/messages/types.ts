// 消息列表的视图类型（spec 0003）。在这里定死，第 8 项把接口数据转换成它们。
import type { UiError } from "@/lib/api/errors";

export type MessageView = Readonly<{
  id: string; // Discord 雪花 ID 用字符串，因为超过 JS 安全整数
  authorId: string;
  authorName: string; // message.author_display_name
  authorAvatarUrl: string | null; // avatarUrl(author_id, discord_user.avatar_hash)
  createdAt: string; // ISO 8601，UTC
  content: string;
  embeds: readonly EmbedView[];
  forwarded: ForwardedView | null;
  attachments: readonly AttachmentView[]; // 只含 removed_at 为空的
  editedAt: string | null; // version > 1 时有值
  deleted: boolean; // deleted_at 不为空
  replyTo: ReplyView | null; // 不是回复时为 null
}>;

// 被回复的消息不在库里时 missing 为 true（spec 0008 AC-20）。
export type ReplyView =
  | Readonly<{ missing: true }>
  | Readonly<{ missing: false; authorName: string; preview: string; deleted: boolean }>;

export type MentionResolver = Readonly<{
  user: (id: string) => string | undefined;
  channel: (id: string) => string | undefined;
}>;

export type EmbedView = Readonly<{
  type: string | null;
  title: string | null;
  description: string | null;
  url: string | null;
  color: number | null; // 0xRRGGBB 整数
  timestamp: string | null; // ISO 8601
  author: Readonly<{ name: string; url: string | null }> | null;
  fields: readonly Readonly<{ name: string; value: string; inline: boolean }>[];
  footerText: string | null;
  imageUrl: string | null;
  thumbnailUrl: string | null;
  // Discord 托管的 embed 图有保存状态；第三方图片没有，继续按 imageUrl 直接加载。
  imageMedia: MediaView | null;
  thumbnailMedia: MediaView | null;
}>;

export type ForwardedView = Readonly<{
  messageId: string;
  channelId: string;
  guildId: string | null;
  content: string;
  embeds: readonly EmbedView[];
  attachments: readonly ForwardedAttachmentView[];
}>;

export type AttachmentView = Readonly<{
  id: string;
  filename: string;
  contentType: string | null;
  sizeBytes: number; // 附件大小在安全整数范围内，可以用 number
  media: MediaView | null; // 保存状态；没有 media 行时为 null，按原来的文件条显示
}>;

// 附件或 embed 图的保存状态（spec 0011）。文件通过 /api/media/{id} 读，不再碰 Discord 原链接。
export type MediaStatus = "pending" | "stored" | "failed" | "too_large" | "no_space" | "gone";

export type MediaView = Readonly<{
  id: string;
  status: MediaStatus;
  filename: string;
  contentType: string | null; // 已保存的用实际类型，否则是 Discord 给的
  sizeBytes: number | null;
  width: number | null;
  height: number | null;
  hasThumb: boolean;
}>;

// 转发里的附件：上线前入库的转发只有文件名，没有 media。
export type ForwardedAttachmentView = Readonly<{
  filename: string;
  media: MediaView | null;
}>;

export type MessageListProps = Readonly<{
  messages: readonly MessageView[]; // 按 id 从旧到新
  resolver: MentionResolver;
  now: Date; // 页面用 useNow() 每 60 秒更新后传入
  timeZone: string; // Intl.DateTimeFormat().resolvedOptions().timeZone
  hasOlder: boolean;
  isLoadingOlder: boolean;
  olderError?: UiError; // 加载旧消息失败，只在顶部显示
  onReachTop: () => void;
  status: "loading" | "ready" | "empty" | "error"; // 只描述首次加载
  error?: UiError; // status 为 error 时必有
  onRetry?: () => void; // 首次加载和旧消息加载共用
}>;
