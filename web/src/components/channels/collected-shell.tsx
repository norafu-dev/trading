"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/app-shell";

import { UnreadProvider, useUnreadCounts } from "./unread-provider";
import { useCollected } from "./use-collected";

const EMPTY: readonly never[] = [];

type ShellLinks = Readonly<{ accountLink: ReactNode; homeLink: ReactNode; children: ReactNode }>;

function ConnectedShell({ accountLink, homeLink, children }: ShellLinks) {
  const { data } = useCollected();
  const unreadCounts = useUnreadCounts();
  return (
    <AppShell
      guilds={data?.guilds ?? EMPTY}
      channels={data?.channels ?? EMPTY}
      collectorStatus="unknown"
      accountLink={accountLink}
      homeLink={homeLink}
      unreadCounts={unreadCounts}
    >
      {children}
    </AppShell>
  );
}

// 把已开启的频道和未读状态接到外壳上。加载中或失败时侧栏是空状态，不挡住页面内容。
export function CollectedShell({ accountLink, homeLink, children }: ShellLinks) {
  return (
    <UnreadProvider>
      <ConnectedShell accountLink={accountLink} homeLink={homeLink}>
        {children}
      </ConnectedShell>
    </UnreadProvider>
  );
}
