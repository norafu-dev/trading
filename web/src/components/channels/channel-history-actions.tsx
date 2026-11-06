"use client";

import { HistoryIcon, WrenchIcon } from "lucide-react";
import { type ReactNode, useState } from "react";

import { gapNotice } from "@/components/history/describe-history";
import { NewJobDialog } from "@/components/history/new-job-dialog";
import { useFillGaps, useHistoryGaps } from "@/components/history/use-history";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

// 频道页的缺口提示和两个按钮（spec 0012 AC-15、AC-18）：
// 有缺口时显示段数和总时长，可以一键补；任何频道都能从这里建历史采集任务。
export function ChannelHistoryActions({
  channelId,
  channelName,
  actions,
}: Readonly<{ channelId: string; channelName: string | undefined; actions?: ReactNode }>) {
  const gaps = useHistoryGaps(channelId);
  const fill = useFillGaps();
  const [dialogOpen, setDialogOpen] = useState(false);
  const notice = gapNotice(gaps.data?.[0]);
  const name = channelName ?? "这个频道";

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2">
      {notice ? (
        <>
          <p className="min-w-0 text-sm text-muted-foreground">{notice}</p>
          <Button
            variant="outline"
            size="sm"
            disabled={fill.isPending}
            aria-label={`补缺口：#${name}`}
            onClick={() => fill.mutate([channelId])}
          >
            {fill.isPending ? (
              <Spinner data-icon="inline-start" />
            ) : (
              <WrenchIcon data-icon="inline-start" aria-hidden />
            )}
            补缺口
          </Button>
        </>
      ) : null}
      <div className="ml-auto flex items-center gap-1">
        {actions}
        <Button variant="ghost" size="sm" onClick={() => setDialogOpen(true)}>
          <HistoryIcon data-icon="inline-start" aria-hidden />
          抓这个频道的历史
        </Button>
      </div>
      <NewJobDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialChannelIds={[channelId]}
      />
    </div>
  );
}
