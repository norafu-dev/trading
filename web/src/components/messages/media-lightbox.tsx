"use client";

import { DownloadIcon } from "lucide-react";

import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

import { mediaUrl } from "./media-url";

type MediaLightboxProps = Readonly<{
  id: string;
  filename: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}>;

// 点开缩略图后看原图，并提供「下载原文件」（spec 0011 AC-15）。
export function MediaLightbox({ id, filename, open, onOpenChange }: MediaLightboxProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-fit max-w-[min(95vw,1200px)] sm:max-w-[min(95vw,1200px)]">
        <DialogTitle className="truncate pr-8">{filename}</DialogTitle>
        {/* biome-ignore lint/performance/noImgElement: 静态导出不做图片优化 */}
        <img
          src={mediaUrl(id)}
          alt={filename}
          className="mx-auto max-h-[70vh] max-w-full rounded-md object-contain"
        />
        <a
          href={mediaUrl(id, { download: true })}
          className="inline-flex items-center gap-1.5 self-start text-sm text-link hover:underline"
        >
          <DownloadIcon aria-hidden strokeWidth={1.75} className="size-4" />
          下载原文件
        </a>
      </DialogContent>
    </Dialog>
  );
}
