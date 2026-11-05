"use client";

import { cn } from "cn";
import {
  BanIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  HistoryIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCwIcon,
} from "lucide-react";
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
  ACTION_LABELS,
  type BatchAction,
  batchActions,
  batchStatusSummary,
  type ChannelGroup,
  channelActions,
  ERROR_LABELS,
  FINISHED_RANGE_LABELS,
  type FinishedRange,
  groupByBatch,
  groupByChannel,
  type HistoryBatch,
  type HistoryJob,
  type JobAction,
  type JobStatus,
  jobActions,
  ORIGIN_LABELS,
  percent,
  STATUS_LABELS,
  WAIT_LABELS,
} from "./describe-history";
import { HistoryStatusBar } from "./history-status-bar";
import { NewJobDialog } from "./new-job-dialog";
import {
  useActiveHistoryJobs,
  useBatchAction,
  useChannelAction,
  useFinishedHistoryJobs,
  useHistoryStatus,
  useJobAction,
} from "./use-history";

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

const ACTION_ICONS: Readonly<Record<JobAction, typeof PauseIcon>> = {
  pause: PauseIcon,
  resume: PlayIcon,
  cancel: BanIcon,
  retry: RotateCwIcon,
};

function JobActions({ job }: Readonly<{ job: HistoryJob }>) {
  const act = useJobAction();
  const actions = jobActions(job.status);
  if (actions.length === 0) return null;
  return (
    <div className="ml-auto flex items-center gap-1">
      {actions.map((action) => {
        const Icon = ACTION_ICONS[action];
        return (
          <Button
            key={action}
            variant="ghost"
            size="sm"
            disabled={act.isPending}
            aria-label={`${ACTION_LABELS[action]}：#${job.channel_name}`}
            onClick={() => act.mutate({ jobId: job.id, action })}
          >
            <Icon data-icon="inline-start" aria-hidden />
            {ACTION_LABELS[action]}
          </Button>
        );
      })}
    </div>
  );
}

function JobRow({
  job,
  time,
  actions = true,
}: Readonly<{ job: HistoryJob; time: TimeFormat; actions?: boolean }>) {
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
        {actions ? <JobActions job={job} /> : null}
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

function SubJobRow({
  job,
  time,
  actions,
}: Readonly<{ job: HistoryJob; time: TimeFormat; actions: boolean }>) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
      <span className="text-muted-foreground">
        {time.fullTime(job.range_start)} 至 {time.fullTime(job.range_end)}
      </span>
      <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABELS[job.status]}</Badge>
      <span className="text-xs text-muted-foreground tabular-nums">
        {percent(job.progress)}% · 已抓 {job.fetched_count} 条
      </span>
      {job.error ? (
        <span className="text-xs text-destructive">{ERROR_LABELS[job.error]}</span>
      ) : null}
      {actions ? <JobActions job={job} /> : null}
    </li>
  );
}

function ChannelActions({ batchId, group }: Readonly<{ batchId: string; group: ChannelGroup }>) {
  const act = useChannelAction();
  return (
    <div className="ml-auto flex items-center gap-1">
      {channelActions(group).map((action) => {
        const Icon = ACTION_ICONS[action];
        return (
          <Button
            key={action}
            variant="ghost"
            size="sm"
            disabled={act.isPending}
            aria-label={`${ACTION_LABELS[action]}：#${group.channelName}`}
            onClick={() => act.mutate({ batchId, channelId: group.channelId, action })}
          >
            <Icon data-icon="inline-start" aria-hidden />
            {ACTION_LABELS[action]}
          </Button>
        );
      })}
    </div>
  );
}

// 一个频道被拆成多段时只显示一个总进度，子任务折叠；有失败的子任务时默认展开（AC-14）。
function ChannelRow({
  batchId,
  group,
  time,
  actions,
}: Readonly<{ batchId: string; group: ChannelGroup; time: TimeFormat; actions: boolean }>) {
  const [open, setOpen] = useState(group.counts.failed > 0);
  const value = percent(group.progress);
  const first = group.jobs[0] as HistoryJob;
  const listId = `history-sub-${batchId}-${group.channelId}`;
  return (
    <li className="flex flex-col gap-2 px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href={channelHref(group.channelId)}
          className="min-w-0 truncate font-semibold hover:underline"
        >
          <span className="text-muted-foreground">{group.guildName} · </span>#{group.channelName}
        </Link>
        <Badge variant={STATUS_VARIANT[group.status]}>{STATUS_LABELS[group.status]}</Badge>
        <span className="text-xs text-muted-foreground">
          {ORIGIN_LABELS[first.origin]}
          {first.refetch ? " · 整段重抓" : ""}
        </span>
        {actions ? <ChannelActions batchId={batchId} group={group} /> : null}
      </div>
      <p className="text-sm text-muted-foreground">
        {time.fullTime(group.rangeStart)} 至 {time.fullTime(group.rangeEnd)}
      </p>
      <div className="flex items-center gap-3">
        <div
          role="progressbar"
          aria-label={`#${group.channelName} 进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={value}
          className="h-2 flex-1 overflow-hidden rounded-full bg-muted"
        >
          <div
            style={{ width: `${value}%` }}
            className={cn("h-full", group.counts.failed > 0 ? "bg-destructive" : "bg-primary")}
          />
        </div>
        <span className="w-10 text-right text-sm font-medium tabular-nums">{value}%</span>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>已抓 {group.fetchedCount} 条</span>
        {group.cursorAt ? <span>抓到 {time.fullTime(group.cursorAt)}</span> : null}
        {group.waiting ? (
          <span>
            {WAIT_LABELS[group.waiting.reason]}，预计 {time.fullTime(group.waiting.until)} 继续
          </span>
        ) : null}
      </p>
      {group.error ? (
        <p role="alert" className="text-sm text-destructive">
          {group.counts.failed} 段失败：{ERROR_LABELS[group.error]}
        </p>
      ) : null}
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
        className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? (
          <ChevronDownIcon className="size-3.5" aria-hidden />
        ) : (
          <ChevronRightIcon className="size-3.5" aria-hidden />
        )}
        共 {group.jobs.length} 段{open ? "，收起" : "，展开"}
      </button>
      {open ? (
        <ul id={listId} className="divide-y divide-border border-l-2 border-border pl-4">
          {group.jobs.map((job) => (
            <SubJobRow key={job.id} job={job} time={time} actions={actions} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function BatchActions({ batch }: Readonly<{ batch: HistoryBatch }>) {
  const act = useBatchAction();
  const actions = batchActions(batch);
  return (
    <div className="flex items-center gap-1">
      {actions.map((action: BatchAction) => (
        <Button
          key={action}
          variant="outline"
          size="xs"
          disabled={act.isPending}
          onClick={() => act.mutate({ batchId: batch.batch_id, action })}
        >
          整批{ACTION_LABELS[action]}
        </Button>
      ))}
    </div>
  );
}

function BatchCard({
  batch,
  jobs,
  time,
  actions = true,
}: Readonly<{
  batch: HistoryBatch | undefined;
  jobs: readonly HistoryJob[];
  time: TimeFormat;
  actions?: boolean;
}>) {
  const first = jobs[0];
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card text-card-foreground">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-5 py-3">
        <h3 className="text-sm font-semibold">
          {first ? `${time.fullTime(first.created_at)} 建的任务` : "任务"}
        </h3>
        {batch ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <p className="text-xs text-muted-foreground">
              {batch.channel_count} 个频道 · 共 {batch.fetched_count} 条 ·{" "}
              {batchStatusSummary(batch)}
            </p>
            {actions ? <BatchActions batch={batch} /> : null}
          </div>
        ) : null}
      </header>
      <ul className="divide-y divide-border">
        {groupByChannel(jobs).map((group) =>
          group.jobs.length === 1 ? (
            <JobRow
              key={group.channelId}
              job={group.jobs[0] as HistoryJob}
              time={time}
              actions={actions}
            />
          ) : (
            <ChannelRow
              key={group.channelId}
              batchId={first?.batch_id ?? ""}
              group={group}
              time={time}
              actions={actions}
            />
          ),
        )}
      </ul>
    </section>
  );
}

// 「已结束」：默认最近 7 天，分页加载，可以切到保留期内的全部（AC-14）。
function FinishedSection({ time }: Readonly<{ time: TimeFormat }>) {
  const [range, setRange] = useState<FinishedRange>("week");
  const finished = useFinishedHistoryJobs(range);
  const pages = finished.data?.pages ?? [];
  const jobs = pages.flatMap((p) => p.jobs);
  const batches = new Map(pages.flatMap((p) => p.batches).map((b) => [b.batch_id, b]));

  let body: React.ReactNode;
  if (finished.isPending) {
    body = (
      <div className="grid h-24 place-items-center">
        <Spinner />
      </div>
    );
  } else if (finished.isError) {
    body = <ErrorBlock error={toUiError(finished.error)} onRetry={() => void finished.refetch()} />;
  } else if (jobs.length === 0) {
    body = (
      <p className="rounded-lg border border-dashed border-border py-6 text-center text-sm text-muted-foreground">
        {FINISHED_RANGE_LABELS[range]}内没有结束的任务
      </p>
    );
  } else {
    body = (
      <div className="flex flex-col gap-4">
        {groupByBatch(jobs).map((group) => (
          <BatchCard
            key={group.batchId}
            batch={batches.get(group.batchId)}
            jobs={group.jobs}
            time={time}
            actions={false}
          />
        ))}
        {finished.hasNextPage ? (
          <Button
            variant="outline"
            className="self-center"
            disabled={finished.isFetchingNextPage}
            onClick={() => void finished.fetchNextPage()}
          >
            {finished.isFetchingNextPage ? <Spinner data-icon="inline-start" /> : null}
            加载更多
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <section aria-labelledby="history-finished-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="history-finished-title" className="text-xl font-semibold">
          已结束
        </h2>
        <fieldset className="flex items-center gap-1">
          <legend className="sr-only">已结束的时间范围</legend>
          {(Object.keys(FINISHED_RANGE_LABELS) as FinishedRange[]).map((key) => (
            <Button
              key={key}
              size="sm"
              variant={range === key ? "secondary" : "ghost"}
              aria-pressed={range === key}
              onClick={() => setRange(key)}
            >
              {FINISHED_RANGE_LABELS[key]}
            </Button>
          ))}
        </fieldset>
      </div>
      {body}
    </section>
  );
}

// 「历史采集」页（spec 0012 AC-1、AC-9、AC-14）：新建任务、进行中的任务和已结束的任务，按批次分组。
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

        <FinishedSection time={time} />
      </div>
      <NewJobDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}
