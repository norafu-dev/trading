"use client";

import { SearchIcon } from "lucide-react";
import Link from "next/link";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

// 图标栏的「搜索」入口（spec 0013 AC-1）。
export function SearchRailLink() {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Link
            href="/search"
            aria-label="搜索"
            className="flex size-10 items-center justify-center rounded-lg transition-colors hover:bg-rail-foreground/10"
          />
        }
      >
        <SearchIcon aria-hidden className="size-5" strokeWidth={1.75} />
      </TooltipTrigger>
      <TooltipContent side="right">搜索</TooltipContent>
    </Tooltip>
  );
}
