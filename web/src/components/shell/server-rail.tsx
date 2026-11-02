"use client";

import { cn } from "cn";
import { ShieldIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Separator } from "@/components/ui/separator";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { fallbackColorFor, guildIconUrl, initialFor } from "@/lib/discord-cdn";

import { CollectorStatusDot } from "./collector-status-dot";
import type { CollectorStatus, GuildInput } from "./types";

type ServerRailProps = Readonly<{
  guilds: readonly GuildInput[];
  selectedGuildId: string | null;
  unreadGuildIds?: ReadonlySet<string>;
  onSelectGuild: (guildId: string) => void;
  collectorStatus: CollectorStatus;
  /** 账号入口要取数，由真实页面传入；预览页不传，不发请求。 */
  accountLink?: ReactNode;
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

// 72px 宽的服务器图标栏：顶部是首页，中间是服务器，底部是账号、安全、登出和采集进程状态。
export function ServerRail({
  guilds,
  selectedGuildId,
  unreadGuildIds,
  onSelectGuild,
  collectorStatus,
  accountLink,
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
          const unread = unreadGuildIds?.has(guild.id) ?? false;
          return (
            <li key={guild.id} className="group/guild relative flex w-full justify-center">
              <span
                aria-hidden
                className={cn(
                  "absolute top-1/2 left-0 w-1 -translate-y-1/2 rounded-r-full bg-primary transition-all",
                  selected ? "h-10" : "h-0 group-hover/guild:h-5",
                )}
              />
              {unread ? (
                <span
                  aria-hidden
                  className="absolute top-1/2 left-0 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-rail-foreground"
                />
              ) : null}
              <Tooltip>
                <TooltipTrigger
                  render={
                    <button
                      type="button"
                      aria-label={unread ? `${guild.name}，有未读消息` : guild.name}
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
      {accountLink}
      <Tooltip>
        <TooltipTrigger
          render={
            <Link
              href="/settings/security"
              aria-label="安全"
              className="flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10"
            />
          }
        >
          <ShieldIcon aria-hidden className="size-5" strokeWidth={1.75} />
        </TooltipTrigger>
        <TooltipContent side="right">安全</TooltipContent>
      </Tooltip>
      <LogoutButton />
      <CollectorStatusDot status={collectorStatus} />
    </nav>
  );
}
