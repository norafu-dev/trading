"use client";

import { SearchIcon } from "lucide-react";
import { type ReactNode, useId, useMemo } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { createTimeFormat } from "@/lib/format/time";

type ToolbarProps = Readonly<{
  query: string;
  onQueryChange: (query: string) => void;
  showNoAccess: boolean;
  onShowNoAccessChange: (show: boolean) => void;
  hiddenCount: number;
  syncedAt: string | null;
  /** 「加载线程」按钮，只有没开启任何频道的服务器才有（spec 0008 AC-12）。 */
  threadLoader?: ReactNode;
}>;

export function Toolbar({
  query,
  onQueryChange,
  showNoAccess,
  onShowNoAccessChange,
  hiddenCount,
  syncedAt,
  threadLoader,
}: ToolbarProps) {
  const switchId = useId();
  const format = useMemo(
    () => createTimeFormat(Intl.DateTimeFormat().resolvedOptions().timeZone),
    [],
  );
  return (
    <div className="flex flex-col gap-3">
      <div className="relative">
        <SearchIcon
          aria-hidden
          strokeWidth={1.75}
          className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          type="search"
          aria-label="搜索频道"
          placeholder="搜索频道和线程"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          className="pl-9"
        />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <div className="flex items-center gap-2">
          <Switch
            id={switchId}
            size="sm"
            checked={showNoAccess}
            onCheckedChange={onShowNoAccessChange}
          />
          <Label htmlFor={switchId} className="text-xs">
            显示无权限频道
          </Label>
        </div>
        <span>已隐藏 {hiddenCount} 个无权限频道</span>
        <span className="md:ml-auto">
          {syncedAt === null ? "还没有同步过" : `上次同步于 ${format.fullTime(syncedAt)}`}
        </span>
      </div>
      {threadLoader}
    </div>
  );
}
