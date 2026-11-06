"use client";

import { DownloadIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

// 可以导出的频道类型：文字、公告、公告线程、公开线程、私密线程（AC-1）。
const EXPORTABLE_TYPES: ReadonlySet<number> = new Set([0, 5, 10, 11, 12]);

export function isExportable(channelType: number): boolean {
  return EXPORTABLE_TYPES.has(channelType);
}

export function ExportButton({
  channelType,
  active,
  onClick,
}: Readonly<{ channelType: number | undefined; active: boolean; onClick: () => void }>) {
  if (channelType === undefined || !isExportable(channelType)) return null;
  return (
    <Button variant="ghost" size="sm" aria-pressed={active} onClick={onClick}>
      <DownloadIcon data-icon="inline-start" aria-hidden />
      导出
    </Button>
  );
}
