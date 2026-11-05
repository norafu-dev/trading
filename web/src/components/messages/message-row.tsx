"use client";

import { cn } from "cn";
import { ReplyIcon } from "lucide-react";
import { memo } from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { fallbackColorFor, initialFor } from "@/lib/discord-cdn";
import { MessageContent } from "@/lib/discord-markdown/MessageContent";
import type { ParserStatus } from "@/lib/discord-markdown/use-markdown-parser";
import type { TimeFormat } from "@/lib/format/time";
import type { MessageRowItem } from "./build-rows";
import { Embed } from "./embed";
import { ForwardedBlock } from "./forwarded-block";
import { MediaAttachment } from "./media-attachment";
import type { MentionResolver, ReplyView } from "./types";

type MessageRowProps = Readonly<{
  row: MessageRowItem;
  format: TimeFormat;
  now: Date;
  resolver: MentionResolver;
  parser: ParserStatus;
  /** 跳到上下文的目标：背景高亮，之后淡出（spec 0013 AC-17）。 */
  highlighted?: boolean;
  /** 搜索命中词（spec 0013 AC-13）。 */
  highlightTerms?: readonly string[];
}>;

// 组首上方的引用行。摘要是第三方内容，只当纯文字显示，不解析 markdown。
function ReplyLine({ reply }: Readonly<{ reply: ReplyView }>) {
  return (
    <div className="flex items-center gap-1.5 pl-14 text-[13px] text-muted-foreground">
      <ReplyIcon aria-hidden strokeWidth={1.75} className="size-3.5 shrink-0" />
      <span className="sr-only">回复</span>
      {reply.missing ? (
        <span>原消息未采集</span>
      ) : (
        <>
          <span className="shrink-0 font-semibold">{reply.authorName}</span>
          <span className="truncate">{reply.preview}</span>
          {reply.deleted ? <span className="shrink-0">（已删除）</span> : null}
        </>
      )}
    </div>
  );
}

// 一条消息。组首显示 40px 头像、名字和时间；同组后面的只显示内容，悬停时在左侧空白处显示「HH:mm」。
export const MessageRow = memo(function MessageRow({
  row,
  format,
  now,
  resolver,
  parser,
  highlighted = false,
  highlightTerms,
}: MessageRowProps) {
  const { message, groupStart } = row;
  const fullTime = format.fullTime(message.createdAt);
  const contentProps = { parser, resolver, now, format, highlightTerms };
  const marks =
    message.editedAt || message.deleted ? (
      <>
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
      </>
    ) : null;

  return (
    <article
      aria-label={`${message.authorName}，${fullTime}`}
      className={cn(
        "group/message relative px-4 py-0.5 transition-colors duration-700 motion-reduce:transition-none",
        highlighted ? "bg-primary/15" : "hover:bg-muted/70",
        message.deleted && "opacity-60",
      )}
    >
      {message.replyTo ? <ReplyLine reply={message.replyTo} /> : null}
      <div className="flex gap-4">
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
        <div className="min-w-0 flex-1 text-base">
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
          <MessageContent content={message.content} suffix={marks} {...contentProps} />
          {message.forwarded ? (
            <ForwardedBlock forwarded={message.forwarded} {...contentProps} />
          ) : null}
          {message.embeds.map((embed, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: embed 没有 id，顺序固定
            <Embed key={index} embed={embed} {...contentProps} />
          ))}
          {message.attachments.length > 0 ? (
            <div className="mt-1 flex flex-wrap items-start gap-1.5">
              {message.attachments.map((attachment) => (
                <MediaAttachment
                  key={attachment.id}
                  filename={attachment.filename}
                  sizeBytes={attachment.sizeBytes}
                  media={attachment.media}
                  highlightTerms={highlightTerms}
                />
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </article>
  );
});
