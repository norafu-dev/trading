"use client";

import { HistoryIcon } from "lucide-react";
import Link from "next/link";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

// 图标栏的「历史采集」入口（spec 0012）。限速自动暂停时的红点在下一个里程碑加上。
export function HistoryRailLink() {
  const label = "历史采集";
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/history"
            aria-label={label}
            className="relative flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10"
          />
        }
      >
        <HistoryIcon aria-hidden className="size-5" strokeWidth={1.75} />
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
