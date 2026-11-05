"use client";

import { cn } from "cn";
import { HistoryIcon, PlusIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { channelHref } from "@/components/shell/routes";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";
import { createTimeFormat, type TimeFormat } from "@/lib/format/time";

import {
  batchStatusSummary,
  ERROR_LABELS,
  groupByBatch,
  type HistoryBatch,
  type HistoryJob,
  type JobStatus,
  ORIGIN_LABELS,
  percent,
  STATUS_LABELS,
  WAIT_LABELS,
} from "./describe-history";
import { HistoryStatusBar } from "./history-status-bar";
import { NewJobDialog } from "./new-job-dialog";
import { useActiveHistoryJobs, useHistoryStatus } from "./use-history";

const STATUS_VARIANT: Readonly<
  Record<JobStatus, "default" | "secondary" | "destructive" | "outline">
> = {
  running: "default",
  pending: "secondary",
  paused: "outline",
  done: "secondary",
  failed: "destructive",
  cancelled: "outline",
};

function JobRow({ job, time }: Readonly<{ job: HistoryJob; time: TimeFormat }>) {
  const value = percent(job.progress);
  return (
    <li className="flex flex-col gap-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href={channelHref(job.channel_id)}
          className="min-w-0 truncate font-semibold hover:underline"
        >
          <span className="text-muted-foreground">{job.guild_name} · </span>#{job.channel_name}
        </Link>
        <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABELS[job.status]}</Badge>
        <span className="text-xs text-muted-foreground">
          {ORIGIN_LABELS[job.origin]}
          {job.refetch ? " · 整段重抓" : ""}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        {time.fullTime(job.range_start)} 至 {time.fullTime(job.range_end)}
      </p>
      <div className="flex items-center gap-3">
        <div
          role="progressbar"
          aria-label={`#${job.channel_name} 进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={value}
          className="h-2 flex-1 overflow-hidden rounded-full bg-muted"
        >
          <div
            style={{ width: `${value}%` }}
            className={cn("h-full", job.status === "failed" ? "bg-destructive" : "bg-primary")}
          />
        </div>
        <span className="w-10 text-right text-sm font-medium tabular-nums">{value}%</span>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>已抓 {job.fetched_count} 条</span>
        {job.cursor_at ? <span>抓到 {time.fullTime(job.cursor_at)}</span> : null}
        {job.wait_reason && job.wait_until ? (
          <span>
            {WAIT_LABELS[job.wait_reason]}，预计 {time.fullTime(job.wait_until)} 继续
          </span>
        ) : null}
      </p>
      {job.error ? (
        <p role="alert" className="text-sm text-destructive">
          {ERROR_LABELS[job.error]}
        </p>
      ) : null}
    </li>
  );
}

function BatchCard({
  batch,
  jobs,
  time,
}: Readonly<{ batch: HistoryBatch | undefined; jobs: readonly HistoryJob[]; time: TimeFormat }>) {
  const first = jobs[0];
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card text-card-foreground">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-5 py-3">
        <h3 className="text-sm font-semibold">
          {first ? `${time.fullTime(first.created_at)} 建的任务` : "任务"}
        </h3>
        {batch ? (
          <p className="text-xs text-muted-foreground">
            {batch.channel_count} 个频道 · 共 {batch.fetched_count} 条 · {batchStatusSummary(batch)}
          </p>
        ) : null}
      </header>
      <ul className="divide-y divide-border">
        {jobs.map((job) => (
          <JobRow key={job.id} job={job} time={time} />
        ))}
      </ul>
    </section>
  );
}

// 「历史采集」页（spec 0012 AC-1、AC-14）：新建任务和进行中的任务，按批次分组。
export function HistoryPanel() {
  const jobs = useActiveHistoryJobs();
  const status = useHistoryStatus();
  const [dialogOpen, setDialogOpen] = useState(false);
  const time = useMemo(
    () => createTimeFormat(Intl.DateTimeFormat().resolvedOptions().timeZone),
    [],
  );

  let body: React.ReactNode;
  if (jobs.isPending) {
    body = (
      <div className="grid h-40 place-items-center">
        <Spinner />
      </div>
    );
  } else if (jobs.isError) {
    body = <ErrorBlock error={toUiError(jobs.error)} onRetry={() => void jobs.refetch()} />;
  } else if (jobs.data.jobs.length === 0) {
    body = (
      <EmptyState
        icon={HistoryIcon}
        title="没有进行中的任务"
        description="选几个频道和一段时间，把以前的消息抓回来。已经采齐的时间会自动跳过。"
        action={
          <Button onClick={() => setDialogOpen(true)}>
            <PlusIcon data-icon="inline-start" aria-hidden />
            新建任务
          </Button>
        }
        className="rounded-lg border border-dashed border-border py-10"
      />
    );
  } else {
    const batches = new Map(jobs.data.batches.map((b) => [b.batch_id, b]));
    body = (
      <div className="flex flex-col gap-4">
        {groupByBatch(jobs.data.jobs).map((group) => (
          <BatchCard
            key={group.batchId}
            batch={batches.get(group.batchId)}
            jobs={group.jobs}
            time={time}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-3xl font-bold">历史采集</h1>
            <p className="text-muted-foreground">
              把频道以前的消息抓回来，和实时采集的消息存在一起，同一条只存一份。抓取节奏很慢，降低小号被封的风险。
            </p>
          </div>
          <Button onClick={() => setDialogOpen(true)}>
            <PlusIcon data-icon="inline-start" aria-hidden />
            新建任务
          </Button>
        </header>

        {status.data ? <HistoryStatusBar status={status.data} time={time} /> : null}

        <section aria-labelledby="history-active-title" className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <h2 id="history-active-title" className="text-xl font-semibold">
              进行中
            </h2>
            {jobs.isFetching && !jobs.isPending ? (
              <RefreshCwIcon
                aria-label="正在更新"
                strokeWidth={1.75}
                className="size-4 animate-spin text-muted-foreground"
              />
            ) : null}
          </div>
          {body}
        </section>
      </div>
      <NewJobDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
