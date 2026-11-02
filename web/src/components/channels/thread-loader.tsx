"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import { GUILDS_KEY } from "@/lib/events/event-stream";

import type { GuildSummary } from "./use-guilds";

const THREAD_TYPES: ReadonlySet<number> = new Set([10, 11, 12]);

export type ThreadLoaderState = Readonly<{ label: string; disabled: boolean }>;

// 请求排队时：账号在线显示正在加载，离线说明要等上线（AC-12）。
export function threadLoaderState(pending: boolean, online: boolean): ThreadLoaderState {
  if (!pending) return { label: "加载线程", disabled: false };
  return { label: online ? "正在加载" : "账号上线后加载", disabled: true };
}

type ThreadLoaderProps = Readonly<{
  guild: GuildSummary;
  online: boolean;
  channels: readonly Readonly<{ type: number }>[];
}>;

// 没开启频道的服务器不订阅，所以线程可能不全；这个按钮让 collector 订阅一次并同步线程。
export function ThreadLoader({ guild, online, channels }: ThreadLoaderProps) {
  const queryClient = useQueryClient();
  const load = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST(
        "/api/discord/guilds/{guild_id}/load-threads",
        {
          params: { path: { guild_id: guild.id } },
        },
      );
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: GUILDS_KEY }),
    onError: (error) => {
      toast.error(toUiError(error).title);
    },
  });
  const { label, disabled } = threadLoaderState(
    guild.thread_load_pending || load.isPending,
    online,
  );
  const noThreads = !channels.some((channel) => THREAD_TYPES.has(channel.type));
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => load.mutate()}>
        {label}
      </Button>
      {noThreads ? <span>这个服务器还没订阅，线程可能不全</span> : null}
    </div>
  );
}
