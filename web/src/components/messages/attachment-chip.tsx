import { PaperclipIcon } from "lucide-react";

import { formatBytes } from "@/lib/format/bytes";

type AttachmentChipProps = Readonly<{ filename: string; sizeBytes?: number }>;

// 附件小标签：回形针、文件名、大小。文件下载和预览由第 12 项接上。
export function AttachmentChip({ filename, sizeBytes }: AttachmentChipProps) {
  return (
    <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-secondary px-3 py-1 text-xs text-secondary-foreground">
      <PaperclipIcon aria-hidden strokeWidth={1.75} className="size-3.5 shrink-0" />
      <span className="truncate font-medium">{filename}</span>
      {sizeBytes !== undefined ? (
        <span className="shrink-0 text-muted-foreground">{formatBytes(sizeBytes)}</span>
      ) : null}
    </span>
  );
}
