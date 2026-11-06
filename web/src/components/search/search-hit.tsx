"use client";

import { ArrowUpRightIcon } from "lucide-react";
import Link from "next/link";
import { memo, useMemo } from "react";

import { MessageRow } from "@/components/messages/message-row";
import { toMessageView } from "@/components/messages/to-message-view";
import type { MentionResolver } from "@/components/messages/types";
import { channelHref } from "@/components/shell/routes";
import { Badge } from "@/components/ui/badge";
import type { ParserStatus } from "@/lib/discord-markdown/use-markdown-parser";
import type { TimeFormat } from "@/lib/format/time";

import type { SearchHitData } from "./use-search";

type SearchHitProps = Readonly<{
  hit: SearchHitData;
  format: TimeFormat;
  now: Date;
  resolver: MentionResolver;
  parser: ParserStatus;
  /** 命中词（不含排除词），用来高亮（AC-13）。 */
  highlightTerms: readonly string[];
}>;

function ChannelMarks({ hit }: Readonly<{ hit: SearchHitData }>) {
  const { channel } = hit;
  return (
    <>
      {channel.status === "deleted" ? <Badge variant="destructive">频道已删除</Badge> : null}
      {channel.status !== "deleted" && !channel.collect_enabled ? (
        <Badge variant="secondary">已关闭采集</Badge>
      ) : null}
    </>
  );
}

// 一条搜索结果：头部是来源，下面是独立的完整消息卡片（AC-12）。
export const SearchHit = memo(function SearchHit({
  hit,
  format,
  now,
  resolver,
  parser,
  highlightTerms,
}: SearchHitProps) {
  const message = useMemo(() => toMessageView(hit.message), [hit.message]);
  const row = useMemo(
    () => ({ kind: "message" as const, key: message.id, message, groupStart: true }),
    [message],
  );
  const { channel, guild } = hit;
  const where = channel.parent ? `${channel.parent.name} › ${channel.name}` : channel.name;
  return (
    <div className="border-b py-2">
      <div className="flex flex-wrap items-center gap-2 px-4 pb-1 text-[13px] text-muted-foreground">
        <span className="font-semibold">{guild.name}</span>
        <span aria-hidden>›</span>
        <span>{where}</span>
        <ChannelMarks hit={hit} />
        <Link
          href={channelHref(channel.id, message.id)}
          className="ml-auto inline-flex items-center gap-0.5 text-primary hover:underline"
        >
          跳到上下文
          <ArrowUpRightIcon aria-hidden className="size-3.5" />
        </Link>
      </div>
      <MessageRow
        row={row}
        format={format}
        now={now}
        resolver={resolver}
        parser={parser}
        highlightTerms={highlightTerms}
      />
    </div>
  );
});
