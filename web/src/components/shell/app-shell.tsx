"use client";

import { cn } from "cn";
import { HashIcon, MenuIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { type ReactNode, Suspense, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { TooltipProvider } from "@/components/ui/tooltip";

import { buildChannelTree, type ChannelInput } from "./build-channel-tree";
import { ChannelSidebar } from "./channel-sidebar";
import { resolveGuildId, sortGuilds } from "./resolve-guild";
import { channelHref } from "./routes";
import { ServerRail } from "./server-rail";
import type { CollectorStatus, GuildChoice, GuildInput } from "./types";

const DESKTOP_QUERY = "(min-width: 768px)";

type AppShellProps = Readonly<{
  guilds: readonly GuildInput[];
  channels: readonly ChannelInput[];
  collectorStatus: CollectorStatus;
  accountLink?: ReactNode;
  /** 频道链接的地址，默认是 `channelHref`；预览页换成指向自己的地址。 */
  hrefFor?: (channelId: string) => string;
  className?: string;
  children: ReactNode;
}>;

type NavigationProps = Readonly<{
  guilds: readonly GuildInput[];
  channels: readonly ChannelInput[];
  collectorStatus: CollectorStatus;
  accountLink?: ReactNode;
  hrefFor: (channelId: string) => string;
  choice: GuildChoice;
  onChoose: (choice: GuildChoice) => void;
  drawerOpen: boolean;
  onDrawerOpenChange: (open: boolean) => void;
  selectedChannelId: string | null;
}>;

function useSelectedChannelId(): string | null {
  return useSearchParams().get("id");
}

// 两层侧栏：宽屏直接显示，窄屏放进从左边滑出的抽屉。
function Navigation(props: NavigationProps) {
  const { guilds, channels, choice, selectedChannelId } = props;
  const sortedGuilds = useMemo(() => sortGuilds(guilds), [guilds]);
  const guildId = resolveGuildId(choice, selectedChannelId, channels, sortedGuilds);
  const groups = useMemo(
    () => buildChannelTree(channels.filter((channel) => channel.guildId === guildId)),
    [channels, guildId],
  );
  const guildName = sortedGuilds.find((guild) => guild.id === guildId)?.name ?? null;

  const panels = (onNavigate?: () => void) => (
    <>
      <ServerRail
        guilds={sortedGuilds}
        selectedGuildId={guildId}
        onSelectGuild={(id) => props.onChoose({ guildId: id, channelId: selectedChannelId })}
        collectorStatus={props.collectorStatus}
        accountLink={props.accountLink}
      />
      <ChannelSidebar
        guildName={guildName}
        groups={groups}
        selectedChannelId={selectedChannelId}
        hrefFor={props.hrefFor}
        onNavigate={onNavigate}
      />
    </>
  );

  return (
    <>
      <div className="hidden h-full md:flex">{panels()}</div>
      <Sheet open={props.drawerOpen} onOpenChange={props.onDrawerOpenChange}>
        <SheetContent
          side="left"
          showCloseButton={false}
          className="flex-row gap-0 border-r-0 p-0 data-[side=left]:w-auto data-[side=left]:sm:max-w-none"
        >
          <SheetTitle className="sr-only">导航</SheetTitle>
          {panels(() => props.onDrawerOpenChange(false))}
        </SheetContent>
      </Sheet>
    </>
  );
}

function UrlNavigation(props: Omit<NavigationProps, "selectedChannelId">) {
  return <Navigation {...props} selectedChannelId={useSelectedChannelId()} />;
}

type TopBarProps = Readonly<{ title: string | null; onOpenDrawer: () => void }>;

// 主区域 48px 顶栏：窄屏左边是菜单按钮，中间是当前频道名。
function TopBar({ title, onOpenDrawer }: TopBarProps) {
  return (
    <header className="relative flex h-12 shrink-0 items-center gap-2 border-b border-border px-2 md:px-4">
      <Button
        variant="ghost"
        size="icon"
        aria-label="打开导航"
        onClick={onOpenDrawer}
        className="md:hidden"
      >
        <MenuIcon aria-hidden strokeWidth={1.75} />
      </Button>
      <h1 className="absolute inset-x-14 flex items-center justify-center gap-1.5 truncate font-semibold md:static md:justify-start">
        {title === null ? (
          "Harvester"
        ) : (
          <>
            <HashIcon
              aria-hidden
              strokeWidth={1.75}
              className="size-4 shrink-0 text-muted-foreground"
            />
            <span className="truncate">{title}</span>
          </>
        )}
      </h1>
    </header>
  );
}

function UrlTopBar({
  channels,
  onOpenDrawer,
}: Readonly<{ channels: readonly ChannelInput[]; onOpenDrawer: () => void }>) {
  const selectedChannelId = useSelectedChannelId();
  const title = channels.find((channel) => channel.id === selectedChannelId)?.name ?? null;
  return <TopBar title={title} onOpenDrawer={onOpenDrawer} />;
}

// 窗口宽度跨过 768px 时关上抽屉。
function useCloseOnBreakpoint(setOpen: (open: boolean) => void) {
  useEffect(() => {
    const query = window.matchMedia(DESKTOP_QUERY);
    const close = () => setOpen(false);
    query.addEventListener("change", close);
    return () => query.removeEventListener("change", close);
  }, [setOpen]);
}

// dashboard 的外壳。读网址查询参数的部分（选中高亮、顶栏标题）单独包在 Suspense 里，
// fallback 是同样结构、没有选中项的版本，静态导出时首屏不会闪空。
export function AppShell({
  guilds,
  channels,
  collectorStatus,
  accountLink,
  hrefFor = channelHref,
  className,
  children,
}: AppShellProps) {
  const [choice, setChoice] = useState<GuildChoice>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  useCloseOnBreakpoint(setDrawerOpen);

  const navigationProps = {
    guilds,
    channels,
    collectorStatus,
    accountLink,
    hrefFor,
    choice,
    onChoose: setChoice,
    drawerOpen,
    onDrawerOpenChange: setDrawerOpen,
  };
  const openDrawer = () => setDrawerOpen(true);

  return (
    <TooltipProvider>
      <div className={cn("flex h-dvh overflow-hidden bg-background", className)}>
        <Suspense fallback={<Navigation {...navigationProps} selectedChannelId={null} />}>
          <UrlNavigation {...navigationProps} />
        </Suspense>
        <div className="flex min-w-0 flex-1 flex-col">
          <Suspense fallback={<TopBar title={null} onOpenDrawer={openDrawer} />}>
            <UrlTopBar channels={channels} onOpenDrawer={openDrawer} />
          </Suspense>
          <main className="relative min-h-0 flex-1 overflow-hidden">{children}</main>
        </div>
      </div>
    </TooltipProvider>
  );
}
