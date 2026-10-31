"use client";

import { cn } from "cn";
import { memo } from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { fallbackColorFor, initialFor } from "@/lib/discord-cdn";
import type { TimeFormat } from "@/lib/format/time";

import type { MessageRowItem } from "./build-rows";
import type { MentionResolver } from "./types";

type MessageRowProps = Readonly<{
  row: MessageRowItem;
  format: TimeFormat;
  now: Date;
  resolver: MentionResolver;
}>;

// 一条消息。组首显示 40px 头像、名字和时间；同组后面的只显示内容，悬停时在左侧空白处显示「HH:mm」。
export const MessageRow = memo(function MessageRow({ row, format, now }: MessageRowProps) {
  const { message, groupStart } = row;
  const fullTime = format.fullTime(message.createdAt);

  return (
    <article
      aria-label={`${message.authorName}，${fullTime}`}
      className={cn(
        "group/message relative flex gap-4 px-4 hover:bg-muted/70",
        "py-0.5",
        message.deleted && "opacity-60",
      )}
    >
      {groupStart ? (
        <Avatar className="mt-0.5 size-10">
          {message.authorAvatarUrl ? <AvatarImage src={message.authorAvatarUrl} alt="" /> : null}
          <AvatarFallback
            className={cn(
              "font-semibold text-avatar-foreground",
              fallbackColorFor(message.authorId),
            )}
          >
            {initialFor(message.authorName)}
          </AvatarFallback>
        </Avatar>
      ) : (
        <time
          dateTime={message.createdAt}
          title={fullTime}
          className="w-10 shrink-0 pt-1 text-center text-xs text-muted-foreground opacity-0 group-hover/message:opacity-100"
        >
          {format.hoverTime(message.createdAt)}
        </time>
      )}
      <div className="min-w-0 flex-1">
        {groupStart ? (
          <header className="flex items-baseline gap-2">
            <span className="truncate text-base font-semibold">{message.authorName}</span>
            <time
              dateTime={message.createdAt}
              title={fullTime}
              className="shrink-0 text-xs text-muted-foreground"
            >
              {format.messageTime(message.createdAt, now)}
            </time>
          </header>
        ) : null}
        <div className="text-base break-words whitespace-pre-wrap [overflow-wrap:anywhere]">
          {message.content}
          {message.editedAt ? (
            <span
              title={`编辑于 ${format.fullTime(message.editedAt)}`}
              className="ml-1 text-xs text-muted-foreground"
            >
              （已编辑）
            </span>
          ) : null}
          {message.deleted ? (
            <Badge variant="destructive" className="ml-2 align-middle">
              已删除
            </Badge>
          ) : null}
        </div>
      </div>
    </article>
  );
});
