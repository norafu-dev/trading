"use client";

import { ArrowLeftIcon, RadioTowerIcon, SearchXIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { useDiscordAccount } from "@/components/discord/use-discord-account";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { toUiError } from "@/lib/api/errors";

import { buildManageTree } from "./build-manage-tree";
import { ChannelTree } from "./channel-tree";
import { GuildList } from "./guild-list";
import { ThreadLoader } from "./thread-loader";
import { Toolbar } from "./toolbar";
import { useGuildChannels } from "./use-guild-channels";
import { type GuildSummary, useGuilds } from "./use-guilds";

const PAGE_PATH = "/settings/channels";

function RowsSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="正在加载" className="flex flex-col gap-2">
      {["a", "b", "c", "d", "e", "f"].map((key) => (
        <Skeleton key={key} className="h-10 w-full" />
      ))}
    </div>
  );
}

function ManagerSkeleton() {
  return (
    <div className="grid gap-6 md:grid-cols-[260px_1fr]">
      <RowsSkeleton />
      <div className="hidden md:block">
        <RowsSkeleton />
      </div>
    </div>
  );
}

function GuildChannels({
  guild,
  syncedAt,
  online,
}: Readonly<{ guild: GuildSummary; syncedAt: string | null; online: boolean }>) {
  const channels = useGuildChannels(guild.id);
  const [query, setQuery] = useState("");
  const [showNoAccess, setShowNoAccess] = useState(false);
  const synced = syncedAt !== null;
  const data = channels.data;
  // 还没同步过时权限未知，不按权限隐藏。
  const tree = useMemo(
    () => buildManageTree(data ?? [], { query, showNoAccess: showNoAccess || !synced }),
    [data, query, showNoAccess, synced],
  );

  if (channels.isError && !data) {
    return <ErrorBlock error={toUiError(channels.error)} onRetry={() => void channels.refetch()} />;
  }
  return (
    <div className="flex flex-col gap-4">
      <Toolbar
        query={query}
        onQueryChange={setQuery}
        showNoAccess={showNoAccess}
        onShowNoAccessChange={setShowNoAccess}
        hiddenCount={tree.hiddenCount}
        syncedAt={syncedAt}
        threadLoader={
          guild.enabled_count === 0 && guild.status === "active" ? (
            <ThreadLoader guild={guild} online={online} channels={data ?? []} />
          ) : null
        }
      />
      {!data ? (
        <RowsSkeleton />
      ) : tree.groups.length > 0 ? (
        <ChannelTree groups={tree.groups} synced={synced} searching={query.trim() !== ""} />
      ) : query.trim() !== "" ? (
        <EmptyState icon={SearchXIcon} title="没有匹配的频道" description="换个关键词试试。" />
      ) : (
        <EmptyState
          icon={RadioTowerIcon}
          title="这个服务器没有可显示的频道"
          description="无权限的频道默认隐藏，可以打开上面的开关查看。"
        />
      )}
    </div>
  );
}

function ManagerBody() {
  const router = useRouter();
  const requested = useSearchParams().get("guild");
  const guilds = useGuilds();
  const account = useDiscordAccount();

  if (guilds.isError && !guilds.data) {
    return <ErrorBlock error={toUiError(guilds.error)} onRetry={() => void guilds.refetch()} />;
  }
  if (account.isError && account.data === undefined) {
    return <ErrorBlock error={toUiError(account.error)} onRetry={() => void account.refetch()} />;
  }
  if (!guilds.data || account.data === undefined) return <ManagerSkeleton />;
  if (account.data === null) {
    return (
      <EmptyState
        icon={RadioTowerIcon}
        title="还没有添加账号"
        description="添加账号并上线后，这里会列出它所在的服务器和频道。"
        action={
          <Link href="/settings/discord" className={buttonVariants({ variant: "secondary" })}>
            去添加账号
          </Link>
        }
      />
    );
  }

  const { guilds: list, synced_at: syncedAt } = guilds.data;
  const offline = account.data.status !== "online";
  const notice = offline ? (
    <Alert>
      <AlertDescription>账号离线时开关会先保存，上线后生效。</AlertDescription>
    </Alert>
  ) : null;
  if (list.length === 0) {
    return (
      <>
        {notice}
        <EmptyState
          icon={RadioTowerIcon}
          title={syncedAt === null ? "还没有同步过" : "小号还没有加入任何服务器"}
          description={syncedAt === null ? "账号上线后会自动同步。" : undefined}
        />
      </>
    );
  }

  const fromUrl = list.some((guild) => guild.id === requested) ? requested : null;
  // 宽屏默认选第一个；窄屏没选中时只显示服务器列表（AC-16）。
  const selectedId = fromUrl ?? list[0]?.id ?? null;
  const selected = list.find((guild) => guild.id === selectedId) ?? null;
  return (
    <>
      {notice}
      <div className="grid gap-6 md:grid-cols-[260px_1fr]">
        <div className={fromUrl === null ? undefined : "hidden md:block"}>
          <GuildList
            guilds={list}
            selectedId={selectedId}
            onSelect={(id) =>
              router.replace(`${PAGE_PATH}?guild=${encodeURIComponent(id)}`, { scroll: false })
            }
          />
        </div>
        <div className={fromUrl === null ? "hidden min-w-0 md:block" : "min-w-0"}>
          <Button
            variant="ghost"
            size="sm"
            className="mb-2 md:hidden"
            onClick={() => router.replace(PAGE_PATH, { scroll: false })}
          >
            <ArrowLeftIcon data-icon="inline-start" aria-hidden />
            返回
          </Button>
          {selected !== null ? (
            // 换服务器时搜索和显示设置都重新开始。
            <GuildChannels
              key={selected.id}
              guild={selected}
              syncedAt={syncedAt}
              online={!offline}
            />
          ) : null}
        </div>
      </div>
    </>
  );
}

// 设置里的「采集频道」页：按服务器看频道树，用开关决定采哪些（spec 0006）。
export function ChannelManager() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-5xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-col gap-1">
          <h1 className="text-3xl font-bold">采集频道</h1>
          <p className="text-muted-foreground">打开开关后几秒内开始采集，不用重启。</p>
        </header>
        <Suspense fallback={<ManagerSkeleton />}>
          <ManagerBody />
        </Suspense>
      </div>
    </div>
  );
}
