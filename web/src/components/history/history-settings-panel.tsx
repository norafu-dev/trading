"use client";

import { type FormEvent, useState } from "react";

import { ErrorBlock } from "@/components/states/error-block";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";

import {
  checkSettings,
  type HistorySettings,
  SETTING_FIELDS,
  type SettingKey,
  type SettingsInput,
} from "./describe-history";
import { useHistorySettings, useSaveHistorySettings } from "./use-history";

function toInput(settings: HistorySettings): SettingsInput {
  return Object.fromEntries(
    SETTING_FIELDS.map(({ key }) => [key, String(settings[key])]),
  ) as SettingsInput;
}

function SettingsForm({ settings }: Readonly<{ settings: HistorySettings }>) {
  const save = useSaveHistorySettings();
  const [input, setInput] = useState<SettingsInput>(() => toInput(settings));
  const [errors, setErrors] = useState<Readonly<Partial<Record<SettingKey, string>>>>({});

  function submit(event: FormEvent) {
    event.preventDefault();
    const check = checkSettings(input);
    if (!check.ok) {
      setErrors(check.errors);
      return;
    }
    setErrors({});
    save.mutate(check.values);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
      <div className="grid gap-4 sm:grid-cols-2">
        {SETTING_FIELDS.map(({ key, label }) => (
          <div key={key} className="flex flex-col gap-2">
            <Label htmlFor={`history-${key}`}>{label}</Label>
            <Input
              id={`history-${key}`}
              inputMode="numeric"
              value={input[key]}
              onChange={(event) => setInput({ ...input, [key]: event.target.value })}
              aria-invalid={errors[key] ? true : undefined}
              aria-describedby={errors[key] ? `history-${key}-error` : undefined}
            />
            {errors[key] ? (
              <p id={`history-${key}-error`} role="alert" className="text-sm text-destructive">
                {errors[key]}
              </p>
            ) : null}
          </div>
        ))}
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

// 设置页「历史采集」（spec 0012 AC-19）：节奏、限额、自动补缺口上限，保存后几秒内生效。
export function HistorySettingsPanel() {
  const settings = useHistorySettings();

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
  const form = SETTING_FIELDS.map(({ key }) => data[key]).join("-");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-col gap-1">
          <h1 className="text-3xl font-bold">历史采集</h1>
          <p className="text-muted-foreground">
            抓取节奏定得很慢，降低小号被封的风险。一次只跑一个任务，每页之间随机等一会儿。
          </p>
        </header>
        <section
          aria-labelledby="history-usage-title"
          className="flex flex-col gap-2 rounded-lg border border-border bg-card px-5 py-4 text-card-foreground"
        >
          <h2 id="history-usage-title" className="text-xl font-semibold">
            最近 24 小时
          </h2>
          <p className="tabular-nums">
            已抓 {data.pages_used_24h} / {data.daily_page_limit} 页
          </p>
        </section>
        <section
          aria-labelledby="history-pace-title"
          className="flex flex-col gap-4 rounded-lg border border-border bg-card px-5 py-4 text-card-foreground"
        >
          <h2 id="history-pace-title" className="text-xl font-semibold">
            节奏和限额
          </h2>
          <SettingsForm key={form} settings={data} />
        </section>
      </div>
    </div>
  );
}
