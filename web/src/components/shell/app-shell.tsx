"use client";

import type { ReactNode } from "react";

// Three areas on wide screens: 72px server rail, 240px channel list, main area.
export function AppShell({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <div className="flex h-dvh overflow-hidden">
      <nav aria-label="服务器" className="hidden w-[72px] shrink-0 bg-rail md:block" />
      <nav aria-label="频道" className="hidden w-60 shrink-0 bg-muted md:block" />
      <main className="flex min-w-0 flex-1 flex-col bg-background">{children}</main>
    </div>
  );
}
