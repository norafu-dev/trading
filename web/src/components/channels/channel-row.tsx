"use client";

import { cn } from "cn";
import { LockIcon, TriangleAlertIcon } from "lucide-react";

import { iconFor } from "@/components/shell/channel-sidebar";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";

import type { ManagedChannel } from "./use-guild-channels";
import { useSetCollect } from "./use-set-collect";

const THREAD_TYPES: ReadonlySet<number> = new Set([10, 11, 12]);

type ChannelRowProps = Readonly<{
  channel: ManagedChannel;
  /** 还没有同步过时读不到权限，不能据此显示无权限或挡住开关（AC-14）。 */
  synced: boolean;
  nested?: boolean;
}>;

export function ChannelRow({ channel, synced, nested = false }: ChannelRowProps) {
  const setCollect = useSetCollect();
  const Icon = iconFor(channel.type);
  const noRead = synced && !channel.can_read;
  const lost = noRead && channel.collect_enabled;
  const blocked = noRead && !channel.collect_enabled;
  return (
    <div
      className={cn(
        "flex min-h-10 items-center gap-2 rounded-md px-2 hover:bg-accent/60",
        nested && "ml-4",
      )}
    >
      <Icon aria-hidden strokeWidth={1.75} className="size-4 shrink-0 text-muted-foreground" />
      <span className={cn("min-w-0 truncate font-medium", blocked && "text-muted-foreground")}>
        {channel.name}
      </span>
      {THREAD_TYPES.has(channel.type) ? <Badge variant="secondary">线程</Badge> : null}
      {channel.status === "archived" ? <Badge variant="secondary">已归档</Badge> : null}
      {blocked ? (
        <Badge variant="outline">
          <LockIcon aria-hidden data-icon="inline-start" />
          无权限
        </Badge>
      ) : null}
      {lost ? (
        <Badge variant="destructive">
          <TriangleAlertIcon aria-hidden data-icon="inline-start" />
          已失去权限，采集已暂停
        </Badge>
      ) : null}
      <Switch
        className="ml-auto"
        aria-label={`采集 #${channel.name}`}
        checked={channel.collect_enabled}
        disabled={blocked}
        onCheckedChange={(enabled) => setCollect.mutate({ channel, enabled })}
      />
    </div>
  );
}
