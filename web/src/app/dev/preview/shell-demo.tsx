"use client";

import { MousePointerClickIcon } from "lucide-react";

import { AppShell } from "@/components/shell/app-shell";
import { EmptyState } from "@/components/states/empty-state";
import { FAKE_CHANNELS, FAKE_GUILDS } from "@/dev/fake-shell";

// 频道链接指回预览页自己（`?id=`），这样点选频道能直接看到高亮和顶栏标题。
function previewHref(channelId: string): string {
  return `/dev/preview?id=${encodeURIComponent(channelId)}#shell`;
}

export function ShellDemo() {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <AppShell
        guilds={FAKE_GUILDS}
        channels={FAKE_CHANNELS}
        collectorStatus="unknown"
        hrefFor={previewHref}
        className="h-[560px]"
      >
        <EmptyState
          icon={MousePointerClickIcon}
          title="从左边选一个频道"
          description="窄屏下点左上角的菜单按钮打开导航。"
          className="h-full"
        />
      </AppShell>
    </div>
  );
}

export function EmptyShellDemo() {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <AppShell guilds={[]} channels={[]} collectorStatus="unknown" className="h-[360px]">
        <EmptyState icon={MousePointerClickIcon} title="从左边选一个频道" className="h-full" />
      </AppShell>
    </div>
  );
}
