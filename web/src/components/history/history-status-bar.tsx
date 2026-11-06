"use client";

import { cn } from "cn";
import { PauseIcon, PlayIcon, SettingsIcon } from "lucide-react";
import Link from "next/link";

import { Button, buttonVariants } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { TimeFormat } from "@/lib/format/time";

import { type HistoryStatus, statusNotices } from "./describe-history";
import { useHistoryControl } from "./use-history";

// 历史页顶部的状态条（spec 0012 AC-6、AC-8）：总开关、暂停原因、24 小时用量和预计继续时间。
export function HistoryStatusBar({
  status,
  time,
}: Readonly<{ status: HistoryStatus; time: TimeFormat }>) {
  const control = useHistoryControl();
  const paused = status.paused_reason !== null;
  const notices = statusNotices(status, time.fullTime);
  const urgent = notices.some((n) => n.tone === "error");
  return (
    <section
      aria-label="历史采集状态"
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-card px-5 py-4 text-card-foreground",
        urgent ? "border-destructive" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground tabular-nums">
          最近 24 小时已抓 {status.pages_used_24h} / {status.daily_page_limit} 页
        </p>
        <div className="flex items-center gap-2">
          <Link
            href="/settings/history"
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            <SettingsIcon data-icon="inline-start" aria-hidden />
            节奏设置
          </Link>
          <Button
            variant={paused ? "default" : "secondary"}
            size="sm"
            disabled={control.isPending}
            onClick={() => control.mutate(!paused)}
          >
            {control.isPending ? (
              <Spinner />
            ) : paused ? (
              <PlayIcon data-icon="inline-start" aria-hidden />
            ) : (
              <PauseIcon data-icon="inline-start" aria-hidden />
            )}
            {paused ? "继续" : "暂停全部"}
          </Button>
        </div>
      </div>
      {notices.map((notice) => (
        <p
          key={notice.key}
          role={notice.tone === "error" ? "alert" : "status"}
          className={cn("text-sm", notice.tone === "error" ? "font-medium text-destructive" : "")}
        >
          {notice.text}
        </p>
      ))}
    </section>
  );
}
