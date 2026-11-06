"use client";

import { HistoryIcon } from "lucide-react";
import Link from "next/link";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { isRateLimitedPause, RATE_LIMITED_LABEL } from "./describe-history";
import { useHistoryStatus } from "./use-history";

// 图标栏的「历史采集」入口：因限速自动暂停时右上角显示红点，等你手动继续（spec 0012 AC-7）。
export function HistoryRailLink() {
  const { data } = useHistoryStatus();
  const limited = isRateLimitedPause(data);
  const label = limited ? RATE_LIMITED_LABEL : "历史采集";
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
        {limited ? (
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
