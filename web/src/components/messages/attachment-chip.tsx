import { DownloadIcon, PaperclipIcon } from "lucide-react";
import type { ReactNode } from "react";

import { formatBytes } from "@/lib/format/bytes";

type AttachmentChipProps = Readonly<{
  filename: string;
  sizeBytes?: number | null;
  // 已保存时是下载地址；没有时只是文字。
  href?: string;
  // 状态文字和重试按钮等。
  children?: ReactNode;
}>;

// 附件小标签：回形针、文件名、大小，已保存的点一下就下载（spec 0011 AC-15）。
export function AttachmentChip({ filename, sizeBytes, href, children }: AttachmentChipProps) {
  const body = (
    <>
      {href ? (
        <DownloadIcon aria-hidden strokeWidth={1.75} className="size-3.5 shrink-0" />
      ) : (
        <PaperclipIcon aria-hidden strokeWidth={1.75} className="size-3.5 shrink-0" />
      )}
      <span className="truncate font-medium">{filename}</span>
      {sizeBytes !== undefined && sizeBytes !== null ? (
        <span className="shrink-0 text-muted-foreground">{formatBytes(sizeBytes)}</span>
      ) : null}
    </>
  );
  const className =
    "inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-secondary px-3 py-1 text-xs text-secondary-foreground";
  return (
    <span className="inline-flex max-w-full flex-wrap items-center gap-2">
      {href ? (
        <a href={href} className={`${className} hover:bg-secondary/80`}>
          {body}
        </a>
      ) : (
        <span className={className}>{body}</span>
      )}
      {children}
    </span>
  );
}
