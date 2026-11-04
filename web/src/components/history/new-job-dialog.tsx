"use client";

import { cn } from "cn";
import { CheckCircle2Icon, InfoIcon, SearchIcon } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { ErrorBlock } from "@/components/states/error-block";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";

import {
  type CreateJobsResult,
  checkRange,
  describeCreate,
  MAX_CHANNELS,
  PRESET_LABELS,
  presetInputs,
  type RangeInputs,
  type RangePreset,
  SKIP_LABELS,
} from "./describe-history";
import { type SelectableGuild, useCreateHistoryJobs, useSelectableChannels } from "./use-history";

const PRESETS: readonly RangePreset[] = ["24h", "7d", "30d", "all"];

type ChannelPickerProps = Readonly<{
  groups: readonly SelectableGuild[];
  selected: ReadonlySet<string>;
  onToggle: (channelId: string) => void;
}>;

function ChannelPicker({ groups, selected, onToggle }: ChannelPickerProps) {
  const [query, setQuery] = useState("");
  const searchId = useId();
  const needle = query.trim().toLowerCase();
  const visible = groups
    .map((g) => ({
      ...g,
      channels: needle
        ? g.channels.filter(
            (c) => c.name.toLowerCase().includes(needle) || g.name.toLowerCase().includes(needle),
          )
        : g.channels,
    }))
    .filter((g) => g.channels.length > 0);
  const full = selected.size >= MAX_CHANNELS;

  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-2 text-sm font-medium">
        频道
        <span className="ml-2 text-xs text-muted-foreground">
          已选 {selected.size} / {MAX_CHANNELS}
        </span>
      </legend>
      <div className="relative">
        <SearchIcon
          aria-hidden
          strokeWidth={1.75}
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          id={searchId}
          aria-label="搜索频道"
          placeholder="搜索服务器或频道"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="pl-9"
        />
      </div>
      <div className="max-h-64 overflow-y-auto rounded-md border border-border">
        {visible.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">没有匹配的频道</p>
        ) : (
          visible.map((guild) => (
            <div key={guild.id} className="flex flex-col py-1">
              <p className="px-3 pt-2 pb-1 text-xs font-medium text-muted-foreground">
                {guild.name}
              </p>
              {guild.channels.map((channel) => {
                const checked = selected.has(channel.id);
                return (
                  <label
                    key={channel.id}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 px-3 py-1.5 text-sm hover:bg-accent",
                      !checked && full && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={checked}
                      disabled={!checked && full}
                      onChange={() => onToggle(channel.id)}
                    />
                    <span className="truncate">
                      <span aria-hidden className="text-muted-foreground">
                        #{" "}
                      </span>
                      {channel.name}
                    </span>
                    {channel.collect_enabled ? null : (
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        未开实时采集
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
          ))
        )}
      </div>
    </fieldset>
  );
}

type RangeFieldsProps = Readonly<{
  inputs: RangeInputs;
  onChange: (inputs: RangeInputs) => void;
}>;

function RangeFields({ inputs, onChange }: RangeFieldsProps) {
  const startId = useId();
  const endId = useId();
  return (
    <fieldset className="flex min-w-0 flex-col gap-3">
      <legend className="mb-2 text-sm font-medium">时间段（本地时间）</legend>
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <Button
            key={preset}
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => onChange(presetInputs(preset, new Date()))}
          >
            {PRESET_LABELS[preset]}
          </Button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor={startId}>开始</Label>
          <Input
            id={startId}
            type="datetime-local"
            value={inputs.start}
            disabled={inputs.allHistory}
            onChange={(event) => onChange({ ...inputs, start: event.target.value })}
          />
          {inputs.allHistory ? (
            <p className="text-xs text-muted-foreground">从每个频道的创建时间开始</p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor={endId}>结束</Label>
          <Input
            id={endId}
            type="datetime-local"
            value={inputs.end}
            onChange={(event) => onChange({ ...inputs, end: event.target.value })}
          />
          <p className="text-xs text-muted-foreground">留空或晚于现在时按现在算</p>
        </div>
      </div>
      {inputs.allHistory ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          className="self-start px-0"
          onClick={() => onChange({ ...inputs, allHistory: false })}
        >
          改为自己填写开始时间
        </Button>
      ) : null}
    </fieldset>
  );
}

function ResultView({
  result,
  names,
}: Readonly<{ result: CreateJobsResult; names: ReadonlyMap<string, string> }>) {
  const created = result.jobs.length > 0;
  return (
    <div className="flex flex-col gap-3" role="status">
      <p className="flex items-center gap-2 text-base font-semibold">
        {created ? (
          <CheckCircle2Icon aria-hidden strokeWidth={1.75} className="size-5" />
        ) : (
          <InfoIcon aria-hidden strokeWidth={1.75} className="size-5" />
        )}
        {describeCreate(result)}
      </p>
      {created ? (
        <p className="text-sm text-muted-foreground">
          已经采齐的时间自动跳过了。任务在后台按顺序慢慢抓，进度会实时显示在列表里。
        </p>
      ) : null}
      {result.skipped.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium">{created ? "这些频道没有建任务：" : "原因："}</p>
          <ul className="flex flex-col gap-1 text-sm">
            {result.skipped.map((s) => (
              <li key={s.channel_id} className="flex justify-between gap-3">
                <span className="truncate">#{names.get(s.channel_id) ?? s.channel_id}</span>
                <span className="shrink-0 text-muted-foreground">{SKIP_LABELS[s.reason]}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

type NewJobDialogProps = Readonly<{
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // 从频道页打开时预先选好的频道（spec 0012 AC-18）。
  initialChannelIds?: readonly string[];
}>;

function NewJobForm({
  initialChannelIds,
  onDone,
}: Readonly<{ initialChannelIds: readonly string[]; onDone: () => void }>) {
  const channels = useSelectableChannels(true);
  const create = useCreateHistoryJobs();
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set(initialChannelIds));
  const [inputs, setInputs] = useState<RangeInputs>(() => presetInputs("24h", new Date()));
  const [refetch, setRefetch] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const refetchId = useId();

  const names = new Map(channels.groups.flatMap((g) => g.channels.map((c) => [c.id, c.name])));

  function toggle(channelId: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(channelId)) next.delete(channelId);
      else if (next.size < MAX_CHANNELS) next.add(channelId);
      return next;
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (selected.size === 0) {
      setFormError("至少选一个频道");
      return;
    }
    const range = checkRange(inputs);
    if (!range.ok) {
      setFormError(range.message);
      return;
    }
    setFormError(null);
    create.mutate({
      channel_ids: [...selected],
      range_start: range.range_start,
      range_end: range.range_end,
      refetch,
    });
  }

  if (create.data) {
    return (
      <>
        <ResultView result={create.data} names={names} />
        <DialogFooter>
          <Button onClick={onDone}>完成</Button>
        </DialogFooter>
      </>
    );
  }

  const submitError = create.error ? toUiError(create.error) : null;

  return (
    <form onSubmit={submit} className="flex min-w-0 flex-col gap-5" noValidate>
      {channels.isPending ? (
        <div className="grid h-40 place-items-center">
          <Spinner />
        </div>
      ) : channels.isError ? (
        <ErrorBlock error={toUiError(channels.error)} className="h-40" />
      ) : channels.groups.length === 0 ? (
        <p className="rounded-md border border-border px-3 py-6 text-center text-sm text-muted-foreground">
          还没有小号能读的频道。先在「频道管理」里同步服务器和频道。
        </p>
      ) : (
        <ChannelPicker groups={channels.groups} selected={selected} onToggle={toggle} />
      )}
      <RangeFields inputs={inputs} onChange={setInputs} />
      <div className="flex items-start gap-3">
        <input
          id={refetchId}
          type="checkbox"
          className="mt-0.5 size-4 accent-primary"
          checked={refetch}
          onChange={(event) => setRefetch(event.target.checked)}
        />
        <div className="flex flex-col gap-0.5">
          <Label htmlFor={refetchId}>整段重抓</Label>
          <p className="text-xs text-muted-foreground">
            不跳过已经采齐的时间，并把这段时间里 Discord 上已经删掉的消息标为已删除。请求会多很多。
          </p>
        </div>
      </div>
      {formError || submitError ? (
        <p role="alert" className="text-sm text-destructive">
          {formError ?? `${submitError?.title}：${submitError?.detail}`}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="button" variant="secondary" onClick={onDone}>
          取消
        </Button>
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? <Spinner /> : null}
          建任务
        </Button>
      </DialogFooter>
    </form>
  );
}

// 新建历史采集任务（spec 0012 AC-1、AC-2）。每次打开都是一张新表单。
export function NewJobDialog({ open, onOpenChange, initialChannelIds = [] }: NewJobDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="text-xl font-semibold">新建历史采集任务</DialogTitle>
          <DialogDescription>
            选频道和时间段，collector 会在后台慢慢把这段时间的消息抓回来。
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <NewJobForm initialChannelIds={initialChannelIds} onDone={() => onOpenChange(false)} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
