"use client";

import { cn } from "cn";
import { PlugIcon } from "lucide-react";
import Link from "next/link";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { describeAccount, STALE_TEXT } from "./account-status";
import { useDiscordAccount } from "./use-discord-account";

const DOT_COLORS = {
  ok: "bg-primary",
  pending: "bg-muted-foreground",
  error: "bg-destructive",
} as const;

// 侧栏的账号入口：图标右下角的小圆点是账号状态，点击进入设置页。
export function AccountRailLink() {
  const account = useDiscordAccount();
  const data = account.data;
  const info = data ? describeAccount(data, (iso) => new Date(iso).toLocaleString()) : null;
  const stale = data?.collector_stale ?? false;
  const label = stale
    ? STALE_TEXT
    : info
      ? `Discord 账号：${info.text}`
      : data === null
        ? "添加 Discord 账号"
        : "Discord 账号";

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/settings/discord"
            aria-label={label}
            className="relative flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10"
          />
        }
      >
        <PlugIcon aria-hidden className="size-5" strokeWidth={1.75} />
        {info ? (
          <span
            aria-hidden
            className={cn(
              "absolute right-1.5 bottom-1.5 size-2.5 rounded-full ring-2 ring-rail",
              DOT_COLORS[stale ? "error" : info.tone],
            )}
          />
        ) : null}
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
