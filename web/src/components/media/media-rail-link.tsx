"use client";

import { HardDriveIcon } from "lucide-react";
import Link from "next/link";

import { useSystemStatus } from "@/components/system/use-system-status";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { STORAGE_FULL_TEXT } from "./describe-media";

// 图标栏的附件存储入口：空间满了（有 no_space 的行）时右上角显示红点（spec 0011 AC-7）。
export function MediaRailLink() {
  const { data } = useSystemStatus();
  const full = data?.media.storage_full ?? false;
  const label = full ? STORAGE_FULL_TEXT : "附件存储";
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/settings/media"
            aria-label={label}
            className="relative flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10"
          />
        }
      >
        <HardDriveIcon aria-hidden className="size-5" strokeWidth={1.75} />
        {full ? (
          <span
            aria-hidden
            className="absolute top-1 right-1 size-2.5 rounded-full bg-destructive ring-2 ring-rail"
          />
        ) : null}
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
