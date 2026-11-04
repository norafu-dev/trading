"use client";

import { cn } from "cn";
import {
  ChevronDownIcon,
  HashIcon,
  type LucideIcon,
  MegaphoneIcon,
  MessagesSquareIcon,
  RadioTowerIcon,
} from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/states/empty-state";
import { buttonVariants } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";

import type { ChannelGroup, ChannelNode } from "./build-channel-tree";

// 接口最多数到 100 条，满了就当「很多」。
const UNREAD_CAP = 100;

export function unreadLabel(count: number): string {
  return count >= UNREAD_CAP ? "99 条以上未读消息" : `${count} 条未读消息`;
}

type ChannelSidebarProps = Readonly<{
  guildName: string | null;
  groups: readonly ChannelGroup[];
  selectedChannelId: string | null;
  unreadCounts?: ReadonlyMap<string, number>;
  hrefFor: (channelId: string) => string;
  onNavigate?: () => void;
}>;

export function iconFor(type: number): LucideIcon {
  if (type === 5) return MegaphoneIcon;
  if (type === 10 || type === 11 || type === 12 || type === 15 || type === 16) {
    return MessagesSquareIcon;
  }
  return HashIcon;
}

type ChannelItemProps = Omit<ChannelSidebarProps, "guildName" | "groups"> &
  Readonly<{ node: ChannelNode }>;

function ChannelItem({
  node,
  selectedChannelId,
  unreadCounts,
  hrefFor,
  onNavigate,
}: ChannelItemProps) {
  const Icon = iconFor(node.type);
  const selected = node.id === selectedChannelId;
  const unread = unreadCounts?.get(node.id) ?? 0;
  const content = (
    <>
      <Icon aria-hidden strokeWidth={1.75} className="size-4 shrink-0" />
      <span className={cn("truncate", unread > 0 && "font-semibold text-foreground")}>
        {node.name}
      </span>
      {unread > 0 ? (
        <span className="ml-auto min-w-5 shrink-0 rounded-full bg-foreground px-1.5 text-center text-xs font-semibold leading-5 text-background">
          <span aria-hidden>{unread >= UNREAD_CAP ? "99+" : unread}</span>
          <span className="sr-only">{unreadLabel(unread)}</span>
        </span>
      ) : null}
    </>
  );
  return (
    <li>
      {node.selectable ? (
        <Link
          href={hrefFor(node.id)}
          prefetch={false}
          aria-current={selected ? "page" : undefined}
          onClick={onNavigate}
          className={cn(
            "flex h-8 items-center gap-1.5 rounded-md px-2 font-medium text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground",
            selected && "bg-accent font-semibold text-accent-foreground hover:bg-accent",
          )}
        >
          {content}
        </Link>
      ) : (
        // 父频道没开采集，只为挂住线程才出现：灰色、不可点。
        <span
          aria-disabled="true"
          className="flex h-8 items-center gap-1.5 px-2 font-medium text-muted-foreground/60"
        >
          {content}
        </span>
      )}
      {node.threads.length > 0 ? (
        <ul className="ml-4 flex flex-col gap-0.5 border-l border-border pl-2">
          {node.threads.map((thread) => (
            <ChannelItem
              key={thread.id}
              node={thread}
              selectedChannelId={selectedChannelId}
              unreadCounts={unreadCounts}
              hrefFor={hrefFor}
              onNavigate={onNavigate}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

// 240px 宽的频道列表：只列出开了采集的频道，按分类分组，线程缩进挂在父频道下。
export function ChannelSidebar({
  guildName,
  groups,
  selectedChannelId,
  unreadCounts,
  hrefFor,
  onNavigate,
}: ChannelSidebarProps) {
  const itemProps = { selectedChannelId, unreadCounts, hrefFor, onNavigate };
  return (
    <nav aria-label="频道" className="flex h-full w-60 shrink-0 flex-col bg-muted">
      <header className="flex h-12 shrink-0 items-center border-b border-border px-4">
        <h2 className="truncate font-semibold">{guildName ?? "Harvester"}</h2>
      </header>
      {groups.length === 0 ? (
        <EmptyState
          icon={RadioTowerIcon}
          title="还没有开启采集的频道"
          description="打开频道的采集开关后，它会出现在这里。"
          action={
            <Link
              href="/settings/channels"
              onClick={onNavigate}
              className={buttonVariants({ variant: "secondary" })}
            >
              管理采集频道
            </Link>
          }
          className="px-4"
        />
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="flex flex-col gap-4 px-2 py-3">
            {groups.map((group) =>
              group.categoryId === null ? (
                <ul key="loose" className="flex flex-col gap-0.5">
                  {group.channels.map((node) => (
                    <ChannelItem key={node.id} node={node} {...itemProps} />
                  ))}
                </ul>
              ) : (
                <Collapsible key={group.categoryId} defaultOpen>
                  <CollapsibleTrigger className="group/category flex w-full items-center gap-1 rounded-md px-1 py-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase hover:text-foreground">
                    <ChevronDownIcon
                      aria-hidden
                      strokeWidth={1.75}
                      className="size-3 transition-transform group-data-[panel-open]/category:rotate-0 -rotate-90"
                    />
                    <span className="truncate">{group.name}</span>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <ul className="mt-0.5 flex flex-col gap-0.5">
                      {group.channels.map((node) => (
                        <ChannelItem key={node.id} node={node} {...itemProps} />
                      ))}
                    </ul>
                  </CollapsibleContent>
                </Collapsible>
              ),
            )}
          </div>
        </ScrollArea>
      )}
    </nav>
  );
}
