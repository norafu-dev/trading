"use client";

import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/app-shell";

import { useCollected } from "./use-collected";

const EMPTY: readonly never[] = [];

// 把已开启的频道接到外壳上。加载中或失败时侧栏是空状态，不挡住页面内容。
export function CollectedShell({
  accountLink,
  children,
}: Readonly<{ accountLink: ReactNode; children: ReactNode }>) {
  const { data } = useCollected();
  return (
    <AppShell
      guilds={data?.guilds ?? EMPTY}
      channels={data?.channels ?? EMPTY}
      collectorStatus="unknown"
      accountLink={accountLink}
    >
      {children}
    </AppShell>
  );
}
