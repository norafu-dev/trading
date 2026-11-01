"use client";

import { cn } from "cn";

import { Badge } from "@/components/ui/badge";

import type { GuildSummary } from "./use-guilds";

type GuildListProps = Readonly<{
  guilds: readonly GuildSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}>;

export function GuildList({ guilds, selectedId, onSelect }: GuildListProps) {
  return (
    <ul className="flex flex-col gap-0.5">
      {guilds.map((guild) => (
        <li key={guild.id}>
          <button
            type="button"
            aria-current={guild.id === selectedId ? "true" : undefined}
            onClick={() => onSelect(guild.id)}
            className={cn(
              "flex min-h-10 w-full items-center gap-2 rounded-md px-3 text-left font-medium transition-colors hover:bg-accent/60",
              guild.id === selectedId && "bg-accent text-accent-foreground hover:bg-accent",
              guild.status === "left" && "text-muted-foreground",
            )}
          >
            <span className="min-w-0 flex-1 truncate">{guild.name}</span>
            {guild.status === "left" ? <Badge variant="outline">已退出</Badge> : null}
            <span className="shrink-0 text-xs text-muted-foreground">
              已开启 {guild.enabled_count}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
