"use client";

import { cn } from "cn";
import { type ReactNode, useState } from "react";

import { HighlightedText } from "@/components/search/highlighted-text";

import { MessageContent } from "@/lib/discord-markdown/MessageContent";
import type { ParserStatus } from "@/lib/discord-markdown/use-markdown-parser";
import type { TimeFormat } from "@/lib/format/time";
import { safeUrl } from "@/lib/safe-url";

import { MediaImage } from "./media-image";
import { MediaStatusText } from "./media-status";
import { RetryMediaButton } from "./retry-media-button";
import type { EmbedView, MediaView, MentionResolver } from "./types";

type EmbedProps = Readonly<{
  embed: EmbedView;
  parser: ParserStatus;
  resolver: MentionResolver;
  now: Date;
  format: TimeFormat;
  highlightTerms?: readonly string[];
}>;

function colorHex(color: number): string {
  return `#${(color & 0xffffff).toString(16).padStart(6, "0")}`;
}

// embed 里的图片可能来自任意网站：不带 referrer、懒加载，加载失败就整个隐藏，不留破图标。
function EmbedImage({ src, className }: Readonly<{ src: string; className: string }>) {
  const [failed, setFailed] = useState(false);
  const url = safeUrl(src);
  if (!url || failed) return null;
  return (
    // biome-ignore lint/performance/noImgElement: 静态导出不做图片优化，embed 图片来自第三方网站
    <img
      src={url.href}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={className}
    />
  );
}

// Discord 托管的 embed 图走自己的存储（spec 0011）：已保存的显示图片，其他显示状态文字。
function EmbedMedia({ media }: Readonly<{ media: MediaView }>) {
  if (media.status === "stored") return <MediaImage media={media} fill />;
  return (
    <span className="flex size-full flex-col items-center justify-center gap-1 p-2 text-center text-xs">
      <MediaStatusText status={media.status} />
      {media.status === "failed" || media.status === "gone" ? (
        <RetryMediaButton mediaId={media.id} />
      ) : null}
    </span>
  );
}

function MaybeLink({
  href,
  className,
  children,
}: Readonly<{ href: string | null; className: string; children: ReactNode }>) {
  const url = safeUrl(href);
  if (!url) return <span className={className}>{children}</span>;
  return (
    <a
      href={url.href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      title={url.href}
      className={cn(className, "hover:underline")}
    >
      {children}
    </a>
  );
}

// embed 卡片（AC-11）：左边色带，作者、标题、描述、字段、大图、右上角缩略图、页脚。
// 图片用固定尺寸的框，加载前后行高不变。
export function Embed({ embed, parser, resolver, now, format, highlightTerms }: EmbedProps) {
  const contentProps = { parser, resolver, now, format, highlightTerms };
  const footer = [
    embed.footerText,
    embed.timestamp ? format.messageTime(embed.timestamp, now) : null,
  ].filter((part): part is string => Boolean(part));

  return (
    <div
      // embed 的颜色是 Discord 给的数据，不是样式，所以用行内 style（spec 0003 的例外）。
      style={embed.color !== null ? { borderLeftColor: colorHex(embed.color) } : undefined}
      className="mt-1 grid max-w-[520px] grid-cols-[minmax(0,1fr)_auto] gap-x-4 rounded-md border border-l-4 border-border bg-card p-3 text-sm text-card-foreground"
    >
      <div className="flex min-w-0 flex-col gap-1.5">
        {embed.author ? (
          <MaybeLink href={embed.author.url} className="text-xs font-semibold">
            {embed.author.name}
          </MaybeLink>
        ) : null}
        {embed.title ? (
          <MaybeLink
            href={embed.url}
            className={cn("font-semibold", safeUrl(embed.url) && "text-link")}
          >
            <HighlightedText text={embed.title} terms={highlightTerms} />
          </MaybeLink>
        ) : null}
        {embed.description ? (
          <MessageContent content={embed.description} {...contentProps} />
        ) : null}
        {embed.fields.length > 0 ? (
          <dl className="mt-1 grid grid-cols-1 gap-2 sm:grid-cols-3">
            {embed.fields.map((field, index) => (
              <div
                // biome-ignore lint/suspicious/noArrayIndexKey: 字段没有 id，顺序固定
                key={index}
                className={cn("min-w-0", !field.inline && "sm:col-span-3")}
              >
                <dt className="text-xs font-semibold">{field.name}</dt>
                <dd>
                  <MessageContent content={field.value} {...contentProps} />
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
      {embed.thumbnailUrl ? (
        <div className="size-20 overflow-hidden rounded-md">
          {embed.thumbnailMedia ? (
            <EmbedMedia media={embed.thumbnailMedia} />
          ) : (
            <EmbedImage src={embed.thumbnailUrl} className="size-full object-cover" />
          )}
        </div>
      ) : null}
      {embed.imageUrl ? (
        <div className="col-span-2 mt-3 aspect-video w-full max-w-[400px] overflow-hidden rounded-md bg-muted">
          {embed.imageMedia ? (
            <EmbedMedia media={embed.imageMedia} />
          ) : (
            <EmbedImage src={embed.imageUrl} className="size-full object-contain" />
          )}
        </div>
      ) : null}
      {footer.length > 0 ? (
        <p className="col-span-2 mt-2 text-xs text-muted-foreground">{footer.join(" · ")}</p>
      ) : null}
    </div>
  );
}
