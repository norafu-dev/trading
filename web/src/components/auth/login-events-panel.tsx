"use client";

import { useQuery } from "@tanstack/react-query";
import { ShieldIcon } from "lucide-react";
import { useMemo } from "react";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { createTimeFormat } from "@/lib/format/time";

type LoginEvent = components["schemas"]["LoginEventOut"];

const OUTCOME_LABELS: Readonly<Record<LoginEvent["outcome"], string>> = {
  success: "成功",
  bad_credentials: "密码或验证码错误",
  rate_limited: "被限速",
  setup_bad_code: "设置码错误",
  setup_completed: "完成设置",
  logout: "登出",
  reset: "重置",
};

function useLoginEvents() {
  return useQuery({
    queryKey: ["auth", "login-events"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/auth/login-events", {
        params: { query: { limit: 50 } },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
  });
}

export function LoginEventsPanel() {
  const events = useLoginEvents();
  const format = useMemo(
    () => createTimeFormat(Intl.DateTimeFormat().resolvedOptions().timeZone),
    [],
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-col gap-1">
          <h1 className="text-3xl font-bold">安全</h1>
          <p className="text-muted-foreground">最近 50 条登录记录，最新的在上面。</p>
        </header>
        {events.isPending ? (
          <div className="flex justify-center py-12">
            <Spinner className="size-6" />
          </div>
        ) : events.isError ? (
          <ErrorBlock error={toUiError(events.error)} onRetry={() => void events.refetch()} />
        ) : events.data.length === 0 ? (
          <EmptyState icon={ShieldIcon} title="还没有登录记录" />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>浏览器</TableHead>
                <TableHead>结果</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.data.map((event) => (
                <TableRow key={`${event.occurred_at}-${event.outcome}-${event.ip}`}>
                  <TableCell className="whitespace-nowrap">
                    {format.fullTime(event.occurred_at)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{event.ip ?? "服务器命令"}</TableCell>
                  <TableCell className="max-w-64 truncate" title={event.user_agent ?? undefined}>
                    {event.user_agent ?? "—"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap">
                    {OUTCOME_LABELS[event.outcome]}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
