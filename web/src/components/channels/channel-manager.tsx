"use client";

import { cn } from "cn";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ErrorBlock } from "@/components/states/error-block";
import { ListSkeleton } from "@/components/states/list-skeleton";
import { Switch } from "@/components/ui/switch";
import { toUiError } from "@/lib/api/errors";

import { useGuildChannels } from "./use-guild-channels";
import { type GuildSummary, useGuilds } from "./use-guilds";
import { useSetCollect } from "./use-set-collect";

function GuildButtons({
  guilds,
  selectedId,
  onSelect,
}: Readonly<{
  guilds: readonly GuildSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}>) {
  return (
    <ul className="flex flex-col gap-1">
      {guilds.map((guild) => (
        <li key={guild.id}>
          <button
            type="button"
            aria-current={guild.id === selectedId ? "true" : undefined}
            onClick={() => onSelect(guild.id)}
            className={cn(
              "flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-accent",
              guild.id === selectedId && "bg-accent",
            )}
          >
            <span className="truncate">{guild.name}</span>
            <span className="shrink-0 text-xs text-muted-foreground">{guild.enabled_count}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ChannelList({ guildId }: Readonly<{ guildId: string }>) {
  const channels = useGuildChannels(guildId);
  const setCollect = useSetCollect();
  if (channels.isError) {
    return <ErrorBlock error={toUiError(channels.error)} onRetry={() => void channels.refetch()} />;
  }
  if (!channels.data) return <ListSkeleton rows={6} />;
  const rows = channels.data.filter((channel) => channel.collectable);
  return (
    <ul className="flex flex-col">
      {rows.map((channel) => (
        <li key={channel.id} className="flex items-center justify-between gap-3 px-3 py-2">
          <span className="truncate text-sm">
            {channel.name}
            {channel.can_read ? null : (
              <span className="ml-2 text-xs text-muted-foreground">无权限</span>
            )}
          </span>
          <Switch
            aria-label={`采集 #${channel.name}`}
            checked={channel.collect_enabled}
            disabled={!channel.can_read && !channel.collect_enabled}
            onCheckedChange={(enabled) => setCollect.mutate({ channel, enabled })}
          />
        </li>
      ))}
    </ul>
  );
}

function ManagerBody() {
  const router = useRouter();
  const params = useSearchParams();
  const guilds = useGuilds();
  if (guilds.isError) {
    return <ErrorBlock error={toUiError(guilds.error)} onRetry={() => void guilds.refetch()} />;
  }
  if (!guilds.data) return <ListSkeleton rows={6} />;
  const list = guilds.data.guilds;
  if (list.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        还没有同步过服务器。先在{" "}
        <Link href="/settings/discord" className="underline">
          账号设置
        </Link>{" "}
        里添加账号，等它上线后会自动同步。
      </p>
    );
  }
  const requested = params.get("guild");
  const selectedId = list.some((guild) => guild.id === requested) ? requested : list[0].id;
  return (
    <div className="grid gap-6 md:grid-cols-[260px_1fr]">
      <GuildButtons
        guilds={list}
        selectedId={selectedId}
        onSelect={(id) => router.replace(`/settings/channels?guild=${id}`)}
      />
      {selectedId ? <ChannelList guildId={selectedId} /> : null}
    </div>
  );
}

// 设置里的「采集频道」页：按服务器看频道，用开关决定采哪些（spec 0006）。
export function ChannelManager() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 p-4 md:p-6">
      <h2 className="text-xl font-semibold">采集频道</h2>
      <Suspense fallback={<ListSkeleton rows={6} />}>
        <ManagerBody />
      </Suspense>
    </div>
  );
}
