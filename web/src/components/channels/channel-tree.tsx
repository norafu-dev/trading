"use client";

import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";

import { iconFor } from "@/components/shell/channel-sidebar";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";

import type { ManageGroup, ManageNode } from "./build-manage-tree";
import { ChannelRow } from "./channel-row";
import { useSetCategoryCollect } from "./use-set-collect";

type TreeProps = Readonly<{ groups: readonly ManageGroup[]; synced: boolean; searching: boolean }>;

function NodeRows({ node, synced }: Readonly<{ node: ManageNode; synced: boolean }>) {
  const { channel, threads } = node;
  const Icon = iconFor(channel.type);
  return (
    <li className="flex flex-col gap-0.5">
      {channel.collectable ? (
        <ChannelRow channel={channel} synced={synced} />
      ) : (
        // 论坛和媒体频道只是分组，帖子各自有开关。
        <div className="flex min-h-10 items-center gap-2 px-2 font-medium text-muted-foreground">
          <Icon aria-hidden strokeWidth={1.75} className="size-4 shrink-0" />
          <span className="truncate">{channel.name}</span>
        </div>
      )}
      {threads.map((thread) => (
        <ChannelRow key={thread.id} channel={thread} synced={synced} nested />
      ))}
    </li>
  );
}

function CategoryGroup({
  group,
  synced,
  searching,
}: Readonly<{ group: ManageGroup; synced: boolean; searching: boolean }>) {
  const [open, setOpen] = useState(true);
  const setCategory = useSetCategoryCollect();
  const category = group.category;
  if (category === null) return null;
  const run = (enabled: boolean) => setCategory.mutate({ categoryId: category.id, enabled });
  return (
    // 搜索时全部展开，否则匹配项可能藏在折叠的分类里。
    <Collapsible open={searching || open} onOpenChange={setOpen}>
      <div className="flex items-center gap-2">
        <CollapsibleTrigger className="group/category flex min-w-0 flex-1 items-center gap-1 rounded-md px-1 py-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase hover:text-foreground">
          <ChevronDownIcon
            aria-hidden
            strokeWidth={1.75}
            className="size-3 -rotate-90 transition-transform group-data-[panel-open]/category:rotate-0"
          />
          <span className="truncate">{category.name}</span>
        </CollapsibleTrigger>
        <Button
          variant="ghost"
          size="sm"
          disabled={setCategory.isPending}
          aria-label={`全部开启 ${category.name}`}
          onClick={() => run(true)}
        >
          全部开启
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={setCategory.isPending}
          aria-label={`全部关闭 ${category.name}`}
          onClick={() => run(false)}
        >
          全部关闭
        </Button>
      </div>
      <CollapsibleContent>
        <ul className="mt-0.5 flex flex-col gap-0.5">
          {group.nodes.map((node) => (
            <NodeRows key={node.channel.id} node={node} synced={synced} />
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ChannelTree({ groups, synced, searching }: TreeProps) {
  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) =>
        group.category === null ? (
          <ul key="loose" className="flex flex-col gap-0.5">
            {group.nodes.map((node) => (
              <NodeRows key={node.channel.id} node={node} synced={synced} />
            ))}
          </ul>
        ) : (
          <CategoryGroup
            key={group.category.id}
            group={group}
            synced={synced}
            searching={searching}
          />
        ),
      )}
    </div>
  );
}
