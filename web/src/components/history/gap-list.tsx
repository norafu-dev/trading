"use client";

import { WrenchIcon } from "lucide-react";
import Link from "next/link";

import { channelHref } from "@/components/shell/routes";
import { ErrorBlock } from "@/components/states/error-block";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";
import type { TimeFormat } from "@/lib/format/time";

import { formatGapDuration, MAX_GAP_CHANNELS } from "./describe-history";
import { useFillGaps, useHistoryGaps } from "./use-history";

// 「缺口」列表（spec 0012 AC-15）：没有被任何覆盖段盖住的时间，可以逐个补或者全部补上。
export function GapList({ time }: Readonly<{ time: TimeFormat }>) {
  const gaps = useHistoryGaps();
  const fill = useFillGaps();
  const channels = gaps.data ?? [];

  let body: React.ReactNode;
  if (gaps.isPending) {
    body = (
      <div className="grid h-24 place-items-center">
        <Spinner />
      </div>
    );
  } else if (gaps.isError) {
    body = <ErrorBlock error={toUiError(gaps.error)} onRetry={() => void gaps.refetch()} />;
  } else if (channels.length === 0) {
    body = (
      <p className="rounded-lg border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
        没有缺口，采集到的时间是连续的
      </p>
    );
  } else {
    body = (
      <ul className="divide-y divide-border overflow-hidden rounded-lg border border-border bg-card text-card-foreground">
        {channels.map((channel) => (
          <li
            key={channel.channel_id}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3"
          >
            <Link
              href={channelHref(channel.channel_id)}
              className="min-w-0 truncate font-medium hover:underline"
            >
              <span className="text-muted-foreground">{channel.guild_name} · </span>#
              {channel.channel_name}
            </Link>
            <span className="text-xs text-muted-foreground">
              {channel.gaps.length} 段 · 共 {formatGapDuration(channel.total_seconds)}
              {channel.gaps[0] ? ` · 最早 ${time.fullTime(channel.gaps[0].start)}` : ""}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              disabled={fill.isPending}
              aria-label={`补缺口：#${channel.channel_name}`}
              onClick={() => fill.mutate([channel.channel_id])}
            >
              <WrenchIcon data-icon="inline-start" aria-hidden />
              补缺口
            </Button>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <section aria-labelledby="history-gaps-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h2 id="history-gaps-title" className="text-xl font-semibold">
            缺口
          </h2>
          <p className="text-sm text-muted-foreground">
            断线、重启或关掉采集留下的时间。不超过设置里的上限时会自动补上。
          </p>
        </div>
        {channels.length > 0 ? (
          <Button
            variant="secondary"
            disabled={fill.isPending}
            onClick={() =>
              fill.mutate(channels.slice(0, MAX_GAP_CHANNELS).map((c) => c.channel_id))
            }
          >
            {fill.isPending ? <Spinner data-icon="inline-start" /> : null}
            全部补上
          </Button>
        ) : null}
      </div>
      {body}
    </section>
  );
}
