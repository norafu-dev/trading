import type { components } from "@/lib/api/schema";
import { avatarUrl } from "@/lib/discord-cdn";

import type { EmbedView, ForwardedView, MessageView, ReplyView } from "./types";

type MessageOut = components["schemas"]["MessageOut"];
type EmbedOut = components["schemas"]["EmbedData"];
type ForwardedOut = NonNullable<MessageOut["forwarded"]>;
type ReplyOut = NonNullable<MessageOut["reply"]>;

function toEmbedView(embed: EmbedOut): EmbedView {
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
  };
}

function toForwardedView(forwarded: ForwardedOut): ForwardedView {
  return {
    messageId: forwarded.message_id,
    channelId: forwarded.channel_id,
    guildId: forwarded.guild_id,
    content: forwarded.content,
    embeds: forwarded.embeds.map(toEmbedView),
    attachmentNames: forwarded.attachment_names,
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
    embeds: message.embeds.map(toEmbedView),
    forwarded: message.forwarded ? toForwardedView(message.forwarded) : null,
    attachments: message.attachments.map((attachment) => ({
      id: attachment.id,
      filename: attachment.filename,
      contentType: attachment.content_type,
      sizeBytes: attachment.size_bytes,
    })),
    editedAt: message.edited_at,
    deleted: message.deleted_at !== null,
    replyTo: message.reply ? toReplyView(message.reply) : null,
  };
}
