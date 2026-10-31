"use client";

import { cn } from "cn";
import Link from "next/link";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fallbackColorFor, guildIconUrl, initialFor } from "@/lib/discord-cdn";

import { CollectorStatusDot } from "./collector-status-dot";
import type { CollectorStatus, GuildInput } from "./types";

type ServerRailProps = Readonly<{
  guilds: readonly GuildInput[];
  selectedGuildId: string | null;
  onSelectGuild: (guildId: string) => void;
  collectorStatus: CollectorStatus;
}>;

function GuildIcon({ guild, selected }: Readonly<{ guild: GuildInput; selected: boolean }>) {
  const src = guildIconUrl(guild.id, guild.iconHash);
  return (
    <Avatar
      className={cn(
        "size-12 rounded-[24px] transition-[border-radius] after:hidden group-hover/guild:rounded-lg",
        selected && "rounded-lg",
      )}
    >
      {src ? <AvatarImage src={src} alt="" className="rounded-[inherit]" /> : null}
      <AvatarFallback
        className={cn(
          "rounded-[inherit] text-sm font-semibold text-avatar-foreground",
          fallbackColorFor(guild.id),
        )}
      >
        {initialFor(guild.name)}
      </AvatarFallback>
    </Avatar>
  );
}

// 72px 宽的服务器图标栏：顶部是首页，中间是服务器，底部是采集进程状态。
export function ServerRail({
  guilds,
  selectedGuildId,
  onSelectGuild,
  collectorStatus,
}: ServerRailProps) {
  return (
    <nav
      aria-label="服务器"
      className="flex h-full w-[72px] shrink-0 flex-col items-center gap-2 bg-rail py-3 text-rail-foreground"
    >
      <Link
        href="/"
        aria-label="Harvester 首页"
        className="flex size-12 items-center justify-center rounded-lg bg-primary text-xl font-bold text-primary-foreground"
      >
        H
      </Link>
      <Separator className="mx-auto bg-rail-foreground/20 data-horizontal:w-8" />
      <ul className="flex min-h-0 w-full flex-1 flex-col items-center gap-2 overflow-y-auto py-1">
        {guilds.map((guild) => {
          const selected = guild.id === selectedGuildId;
          return (
            <li key={guild.id} className="group/guild relative flex w-full justify-center">
              <span
                aria-hidden
                className={cn(
                  "absolute top-1/2 left-0 w-1 -translate-y-1/2 rounded-r-full bg-primary transition-all",
                  selected ? "h-10" : "h-0 group-hover/guild:h-5",
                )}
              />
              <Tooltip>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      aria-label={guild.name}
                      aria-current={selected ? "true" : undefined}
                      onClick={() => onSelectGuild(guild.id)}
                      className="rounded-lg"
                    />
                  }
                >
                  <GuildIcon guild={guild} selected={selected} />
                </TooltipTrigger>
                <TooltipContent side="right">{guild.name}</TooltipContent>
              </Tooltip>
            </li>
          );
        })}
      </ul>
      <CollectorStatusDot status={collectorStatus} />
    </nav>
  );
}
