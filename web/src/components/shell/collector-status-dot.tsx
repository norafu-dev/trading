import { cn } from "cn";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import type { CollectorStatus } from "./types";

const LABELS: Readonly<Record<CollectorStatus, string>> = {
  online: "采集进程在线",
  offline: "采集进程离线",
  unknown: "采集进程状态未知",
};

const COLORS: Readonly<Record<CollectorStatus, string>> = {
  online: "bg-primary",
  offline: "bg-destructive",
  unknown: "bg-muted-foreground",
};

// 心跳由第 9 项提供；在那之前一律传 "unknown"（灰色）。
export function CollectorStatusDot({ status }: Readonly<{ status: CollectorStatus }>) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            role="img"
            aria-label={LABELS[status]}
            className="flex size-12 items-center justify-center rounded-full"
          />
        }
      >
        <span
          className={cn("size-3 rounded-full ring-2 ring-rail-foreground/20", COLORS[status])}
        />
      </TooltipTrigger>
      <TooltipContent side="right">{LABELS[status]}</TooltipContent>
    </Tooltip>
  );
}
