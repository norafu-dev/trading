import type { components } from "@/lib/api/schema";
import { avatarUrl } from "@/lib/discord-cdn";

import type { EmbedView, ForwardedView, MediaView, MessageView, ReplyView } from "./types";

type MessageOut = components["schemas"]["MessageOut"];
type EmbedOut = components["schemas"]["EmbedData"];
type ForwardedOut = NonNullable<MessageOut["forwarded"]>;
type ReplyOut = NonNullable<MessageOut["reply"]>;
type MediaOut = components["schemas"]["MediaOut"];

function toMediaView(media: MediaOut | undefined): MediaView | null {
  if (!media) return null;
  return {
    id: media.id,
    status: media.status,
    filename: media.filename,
    contentType: media.content_type,
    sizeBytes: media.size_bytes,
    width: media.width,
    height: media.height,
    hasThumb: media.has_thumb,
  };
}

// 按 (kind, slot) 找一行：转发附件和 embed 图都用序号对应。
function findSlot(
  media: readonly MediaOut[],
  kind: MediaOut["kind"],
  slot: number,
): MediaView | null {
  return toMediaView(media.find((m) => m.kind === kind && m.slot === slot));
}

function toEmbedView(embed: EmbedOut, media: readonly MediaOut[], slot: number): EmbedView {
  return {
    type: embed.type ?? null,
    title: embed.title ?? null,
    description: embed.description ?? null,
    url: embed.url ?? null,
    color: embed.color ?? null,
    timestamp: embed.timestamp ?? null,
    author: embed.author?.name ? { name: embed.author.name, url: embed.author.url ?? null } : null,
    fields: (embed.fields ?? []).map((field) => ({
      name: field.name,
      value: field.value,
      inline: field.inline ?? false,
    })),
    footerText: embed.footer_text ?? null,
    imageUrl: embed.image_url ?? null,
    thumbnailUrl: embed.thumbnail_url ?? null,
    imageMedia: embed.image_url ? findSlot(media, "embed_image", slot) : null,
    thumbnailMedia: embed.thumbnail_url ? findSlot(media, "embed_thumbnail", slot) : null,
  };
}

function toForwardedView(forwarded: ForwardedOut, media: readonly MediaOut[]): ForwardedView {
  return {
    messageId: forwarded.message_id,
    channelId: forwarded.channel_id,
    guildId: forwarded.guild_id,
    content: forwarded.content,
    // 转发里的 embed 不建 media 行（AC-2 只管消息自己的 embed），所以没有保存状态。
    embeds: forwarded.embeds.map((embed) => toEmbedView(embed, [], 0)),
    attachments: forwarded.attachment_names.map((filename, slot) => ({
      filename,
      media: findSlot(media, "forwarded_attachment", slot),
    })),
  };
}

function toReplyView(reply: ReplyOut): ReplyView {
  if (reply.missing) return { missing: true };
  return {
    missing: false,
    authorName: reply.author_name ?? "",
    preview: reply.preview ?? "",
    deleted: reply.deleted ?? false,
  };
}

export function toMessageView(message: MessageOut): MessageView {
  return {
    id: message.id,
    authorId: message.author_id,
    authorName: message.author_name,
    authorAvatarUrl: avatarUrl(message.author_id, message.author_avatar_hash),
    createdAt: message.created_at,
    content: message.content,
    embeds: message.embeds.map((embed, slot) => toEmbedView(embed, message.media, slot)),
    forwarded: message.forwarded ? toForwardedView(message.forwarded, message.media) : null,
    attachments: message.attachments.map((attachment) => ({
      id: attachment.id,
      filename: attachment.filename,
      contentType: attachment.content_type,
      sizeBytes: attachment.size_bytes,
      media: toMediaView(message.media.find((m) => m.attachment_id === attachment.id)),
    })),
    editedAt: message.edited_at,
    deleted: message.deleted_at !== null,
    replyTo: message.reply ? toReplyView(message.reply) : null,
  };
}
