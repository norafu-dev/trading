"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/app-shell";

import { UnreadProvider, useUnreadChannels } from "./unread-provider";
import { useCollected } from "./use-collected";

const EMPTY: readonly never[] = [];

function ConnectedShell({
  accountLink,
  children,
}: Readonly<{ accountLink: ReactNode; children: ReactNode }>) {
  const { data } = useCollected();
  const unread = useUnreadChannels();
  return (
    <AppShell
      guilds={data?.guilds ?? EMPTY}
      channels={data?.channels ?? EMPTY}
      collectorStatus="unknown"
      accountLink={accountLink}
      unreadChannelIds={unread}
    >
      {children}
    </AppShell>
  );
}

// 把已开启的频道和未读状态接到外壳上。加载中或失败时侧栏是空状态，不挡住页面内容。
export function CollectedShell({
  accountLink,
  children,
}: Readonly<{ accountLink: ReactNode; children: ReactNode }>) {
  return (
    <UnreadProvider>
      <ConnectedShell accountLink={accountLink}>{children}</ConnectedShell>
    </UnreadProvider>
  );
}
