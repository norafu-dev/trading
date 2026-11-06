"use client";

import { cn } from "cn";
import { HardDriveIcon, RotateCwIcon } from "lucide-react";
import { type FormEvent, useState } from "react";

import { ErrorBlock } from "@/components/states/error-block";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";

import {
  COUNT_LABELS,
  checkLimits,
  formatUsedGb,
  type MediaSettings,
  STORAGE_FULL_DETAIL,
  usedPercent,
} from "./describe-media";
import { useMediaSettings, useRetryAllFailed, useSaveMediaLimits } from "./use-media-settings";

function LimitsForm({ settings }: Readonly<{ settings: MediaSettings }>) {
  const save = useSaveMediaLimits();
  const [maxFileMb, setMaxFileMb] = useState(String(settings.max_file_mb));
  const [quotaGb, setQuotaGb] = useState(String(settings.quota_gb));
  const [errors, setErrors] = useState<Readonly<{ maxFileMb?: string; quotaGb?: string }>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const check = checkLimits({ maxFileMb, quotaGb });
    if (!check.ok) {
      setErrors({ maxFileMb: check.maxFileMb, quotaGb: check.quotaGb });
      return;
    }
    setErrors({});
    save.mutate({ max_file_mb: check.maxFileMb, quota_gb: check.quotaGb });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <Label htmlFor="media-max-file">单个文件上限（MB）</Label>
          <Input
            id="media-max-file"
            inputMode="numeric"
            value={maxFileMb}
            onChange={(event) => setMaxFileMb(event.target.value)}
            aria-invalid={errors.maxFileMb ? true : undefined}
            aria-describedby={errors.maxFileMb ? "media-max-file-error" : undefined}
          />
          {errors.maxFileMb ? (
            <p id="media-max-file-error" role="alert" className="text-sm text-destructive">
              {errors.maxFileMb}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <Label htmlFor="media-quota">总容量上限（GB）</Label>
          <Input
            id="media-quota"
            inputMode="numeric"
            value={quotaGb}
            onChange={(event) => setQuotaGb(event.target.value)}
            aria-invalid={errors.quotaGb ? true : undefined}
            aria-describedby={errors.quotaGb ? "media-quota-error" : undefined}
          />
          {errors.quotaGb ? (
            <p id="media-quota-error" role="alert" className="text-sm text-destructive">
              {errors.quotaGb}
            </p>
          ) : null}
        </div>
      </div>
      <div>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? <Spinner /> : null}
          保存
        </Button>
      </div>
    </form>
  );
}

// 设置页「附件存储」区块（spec 0011 AC-8）：上限、已用容量和占比、各状态数量、重试全部失败。
export function MediaSettingsPanel() {
  const settings = useMediaSettings();
  const retryAll = useRetryAllFailed();

  if (settings.isPending) {
    return (
      <div className="grid h-full place-items-center">
        <Spinner />
      </div>
    );
  }
  if (settings.isError) {
    return (
      <ErrorBlock
        error={toUiError(settings.error)}
        onRetry={() => void settings.refetch()}
        className="h-full"
      />
    );
  }
  const data = settings.data;
  const percent = usedPercent(data.used_bytes, data.quota_gb);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-col gap-1">
          <h1 className="text-3xl font-bold">附件存储</h1>
          <p className="text-muted-foreground">
            消息里的图片和文件会下载到自己的存储里，Discord 的链接过期后仍然能看。
          </p>
        </header>

        <section
          aria-labelledby="media-usage-title"
          className={cn(
            "flex flex-col gap-4 rounded-lg border bg-card px-5 py-4 text-card-foreground",
            data.storage_full ? "border-destructive" : "border-border",
          )}
        >
          <h2 id="media-usage-title" className="flex items-center gap-2 text-xl font-semibold">
            <HardDriveIcon aria-hidden strokeWidth={1.75} className="size-5" />
            已用容量
          </h2>
          {data.storage_full ? (
            <p role="alert" className="text-sm font-medium text-destructive">
              {STORAGE_FULL_DETAIL}。调高总容量后会自动继续保存。
            </p>
          ) : null}
          <div className="flex flex-col gap-2">
            <p className="text-sm">
              {formatUsedGb(data.used_bytes)} / {data.quota_gb} GB（{percent}%）
            </p>
            <div
              role="progressbar"
              aria-label="已用容量"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="h-2 w-full overflow-hidden rounded-full bg-muted"
            >
              <div
                style={{ width: `${percent}%` }}
                className={cn("h-full", data.storage_full ? "bg-destructive" : "bg-primary")}
              />
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
            {COUNT_LABELS.map(([key, label]) => (
              <div key={key} className="flex items-baseline justify-between gap-2">
                <dt className="text-sm text-muted-foreground">{label}</dt>
                <dd className="font-semibold tabular-nums">{data.counts[key]}</dd>
              </div>
            ))}
          </dl>
          {data.pending_channel_off > 0 ? (
            <p className="text-sm text-muted-foreground">
              待保存里有 {data.pending_channel_off} 个所在频道已关闭，重新打开频道后会继续。
            </p>
          ) : null}
          <div>
            <Button
              variant="secondary"
              disabled={data.counts.failed === 0 || retryAll.isPending}
              onClick={() => retryAll.mutate()}
            >
              {retryAll.isPending ? (
                <Spinner />
              ) : (
                <RotateCwIcon data-icon="inline-start" aria-hidden />
              )}
              重试全部失败
            </Button>
          </div>
        </section>

        <section
          aria-labelledby="media-limits-title"
          className="flex flex-col gap-4 rounded-lg border border-border bg-card px-5 py-4 text-card-foreground"
        >
          <h2 id="media-limits-title" className="text-xl font-semibold">
            上限
          </h2>
          <LimitsForm key={`${data.max_file_mb}-${data.quota_gb}`} settings={data} />
        </section>
      </div>
    </div>
  );
}
