"use client";

import { useQuery } from "@tanstack/react-query";
import { cn } from "cn";
import Link from "next/link";
import { useMemo } from "react";

import { describeAccount, STALE_TEXT } from "@/components/discord/account-status";
import { useDiscordAccount } from "@/components/discord/use-discord-account";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { api } from "@/lib/api/client";
import { ApiResponseError, toUiError } from "@/lib/api/errors";
import { useEventStream } from "@/lib/events/event-stream";
import { createTimeFormat } from "@/lib/format/time";

function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, response } = await api.GET("/api/health");
      if (!data) throw new ApiResponseError(response.status);
      return data;
    },
    refetchInterval: 15_000,
  });
}

type Tone = "ok" | "pending" | "error";

function StatusRow({
  label,
  tone,
  value,
  detail,
}: Readonly<{ label: string; tone: Tone; value: string; detail?: string }>) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="flex flex-col items-end gap-1 text-right">
        <Badge variant={tone === "ok" ? "default" : tone === "error" ? "destructive" : "secondary"}>
          <span
            aria-hidden
            className={cn(
              "size-1.5 rounded-full",
              tone === "ok" && "bg-primary-foreground",
              tone === "error" && "bg-destructive-foreground",
              tone === "pending" && "bg-muted-foreground",
            )}
          />
          {value}
        </Badge>
        {detail ? <span className="text-xs text-muted-foreground">{detail}</span> : null}
      </dd>
    </div>
  );
}

export function SystemStatus() {
  const health = useHealth();
  const { connected, lastHeartbeat } = useEventStream();
  const account = useDiscordAccount();
  const format = useMemo(
    () => createTimeFormat(Intl.DateTimeFormat().resolvedOptions().timeZone),
    [],
  );

  const apiRow = health.isPending
    ? ({ tone: "pending", value: "检查中" } as const)
    : health.isError
      ? ({ tone: "error", value: "不可用", detail: toUiError(health.error).detail } as const)
      : ({ tone: "ok", value: "正常", detail: `数据库 ${health.data.database}` } as const);

  const accountInfo = account.data ? describeAccount(account.data, format.fullTime) : null;
  const accountRow = account.data?.collector_stale
    ? ({ tone: "error", value: STALE_TEXT } as const)
    : accountInfo
      ? ({ tone: accountInfo.tone, value: accountInfo.text } as const)
      : ({ tone: "pending", value: account.data === null ? "还没有添加" : "检查中" } as const);

  return (
    <section
      aria-labelledby="system-status-title"
      className="rounded-lg border border-border bg-card px-5 py-4 text-card-foreground"
    >
      <h2 id="system-status-title" className="text-xl font-semibold">
        系统状态
      </h2>
      <dl className="mt-2 flex flex-col">
        <StatusRow label="API" {...apiRow} />
        <Separator />
        <StatusRow
          label="数据库时间"
          tone={health.data?.database_time ? "ok" : "pending"}
          value={health.data?.database_time ? format.fullTime(health.data.database_time) : "—"}
        />
        <Separator />
        <StatusRow
          label="实时推送（SSE）"
          tone={connected ? "ok" : "error"}
          value={connected ? "已连接" : "未连接"}
          detail={lastHeartbeat ? `最近心跳 ${format.fullTime(lastHeartbeat)}` : undefined}
        />
        <Separator />
        <Link href="/settings/discord" className="rounded-md hover:bg-muted/50">
          <StatusRow label="Discord 账号" {...accountRow} />
        </Link>
      </dl>
    </section>
  );
}
