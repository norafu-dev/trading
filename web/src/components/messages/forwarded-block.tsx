import { ForwardIcon } from "lucide-react";

import { MessageContent } from "@/lib/discord-markdown/MessageContent";
import type { ParserStatus } from "@/lib/discord-markdown/use-markdown-parser";
import type { TimeFormat } from "@/lib/format/time";

import { AttachmentChip } from "./attachment-chip";
import { Embed } from "./embed";
import type { ForwardedView, MentionResolver } from "./types";

type ForwardedBlockProps = Readonly<{
  forwarded: ForwardedView;
  parser: ParserStatus;
  resolver: MentionResolver;
  now: Date;
  format: TimeFormat;
}>;

// 转发的消息：带「转发」标签的引用块，里面是被转发的文字、embed 和附件文件名。
export function ForwardedBlock({ forwarded, ...contentProps }: ForwardedBlockProps) {
  return (
    <div className="mt-1 flex flex-col gap-1 border-l-4 border-border pl-3">
      <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
        <ForwardIcon aria-hidden strokeWidth={1.75} className="size-3.5" />
        转发
      </span>
      <MessageContent content={forwarded.content} {...contentProps} />
      {forwarded.embeds.map((embed, index) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: embed 没有 id，顺序固定
        <Embed key={index} embed={embed} {...contentProps} />
      ))}
      {forwarded.attachmentNames.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {forwarded.attachmentNames.map((name, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: 同名文件可能重复
            <AttachmentChip key={index} filename={name} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
