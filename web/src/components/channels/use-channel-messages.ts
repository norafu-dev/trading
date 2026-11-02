"use client";

import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toMessageView } from "@/components/messages/to-message-view";
import type { MentionResolver, MessageView } from "@/components/messages/types";
import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import { useMessageEvents } from "@/lib/events/event-stream";

import {
  chunk,
  flatten,
  hasGap,
  type MessagesData,
  type MessagesPage,
  mergeIntoPages,
  newestId,
  type PageParam,
  pageFrom,
  planChanged,
} from "./message-pages";

const PAGE_SIZE = 50;
const MAX_IDS_PER_REQUEST = 100;
// 通知丢了、LISTEN 断了或者订阅者队列溢出时，靠这个轮询补上（AC-19）。
const RECONCILE_INTERVAL_MS = 30_000;

type Query = Readonly<{ before?: string; after?: string; ids?: string; limit?: number }>;

async function fetchPage(channelId: string, query: Query): Promise<MessagesPage> {
  const { data, error, response } = await api.GET("/api/discord/channels/{channel_id}/messages", {
    params: { path: { channel_id: channelId }, query },
  });
  if (!data) throw toApiError(response.status, error);
  return data;
}

// 等着做的事。所有写缓存的动作都在一个循环里串行：TanStack 的 fetchNextPage 结束时会用
// 开始时的快照覆盖缓存，和增量合并同时跑，新消息就丢了。
type Pending = {
  fetchNew: boolean;
  reconcile: boolean;
  older: boolean;
  changed: Set<string>;
};

const noPending = (): Pending => ({
  fetchNew: false,
  reconcile: false,
  older: false,
  changed: new Set(),
});

export type ChannelMessages = Readonly<{
  messages: readonly MessageView[];
  resolver: MentionResolver;
  status: "loading" | "ready" | "empty" | "error";
  error: unknown;
  hasOlder: boolean;
  isLoadingOlder: boolean;
  olderFailed: unknown;
  loadOlder: () => void;
  retry: () => void;
  /** 列表被整页替换时加一；用作 key 重新挂载，列表回到底部。 */
  epoch: number;
}>;

export function useChannelMessages(channelId: string): ChannelMessages {
  const queryClient = useQueryClient();
  const queryKey = useMemo(() => ["discord-messages", channelId] as const, [channelId]);
  const [epoch, setEpoch] = useState(0);

  const query = useInfiniteQuery<
    MessagesPage,
    Error,
    MessagesData,
    readonly [string, string],
    PageParam
  >({
    queryKey,
    initialPageParam: null,
    queryFn: ({ pageParam }) =>
      fetchPage(
        channelId,
        pageParam === null ? { limit: PAGE_SIZE } : { before: pageParam, limit: PAGE_SIZE },
      ),
    getNextPageParam: (last) => (last.has_more ? (last.messages[0]?.id ?? null) : null),
    // 增量都用 setQueryData 写回；自动重取会把已加载的所有页再取一遍（AC-19）。
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    retry: (count, error) => count < 2 && error instanceof TypeError,
  });

  const latestQuery = useRef(query);
  latestQuery.current = query;
  const pending = useRef<Pending>(noPending());
  const running = useRef(false);

  const read = useCallback(
    () => queryClient.getQueryData<MessagesData>(queryKey),
    [queryClient, queryKey],
  );
  const write = useCallback(
    (update: (data: MessagesData) => MessagesData) =>
      queryClient.setQueryData<MessagesData>(queryKey, (data) => (data ? update(data) : data)),
    [queryClient, queryKey],
  );

  // 取最新一页：接得上就合并，接不上就整页替换，旧页丢弃，列表回到底部。
  const reconcile = useCallback(async () => {
    const latest = await fetchPage(channelId, { limit: PAGE_SIZE });
    const data = read();
    if (!data) return;
    if (newestId(data) === null) {
      // 空列表没有东西要保留；has_more 也要换成这一页的。
      queryClient.setQueryData<MessagesData>(queryKey, pageFrom(latest));
    } else if (hasGap(data, latest)) {
      queryClient.setQueryData<MessagesData>(queryKey, pageFrom(latest));
      setEpoch((value) => value + 1);
    } else {
      write((current) => mergeIntoPages(current, latest));
    }
  }, [channelId, queryClient, queryKey, read, write]);

  const fetchNew = useCallback(async () => {
    const data = read();
    const newest = data ? newestId(data) : null;
    if (newest === null) {
      // 列表是空的：没有 after 可用，直接取最新一页。
      await reconcile();
      return;
    }
    const page = await fetchPage(channelId, { after: newest, limit: PAGE_SIZE });
    if (page.has_more) {
      await reconcile();
    } else {
      write((current) => mergeIntoPages(current, page));
    }
  }, [channelId, read, reconcile, write]);

  const fetchChanged = useCallback(
    async (ids: readonly string[]) => {
      const data = read();
      if (!data) return;
      const plan = planChanged(data, ids);
      for (const part of chunk(plan.fetchIds, MAX_IDS_PER_REQUEST)) {
        const page = await fetchPage(channelId, { ids: part.join(",") });
        write((current) => mergeIntoPages(current, page));
      }
      if (plan.needsAfter) await fetchNew();
    },
    [channelId, fetchNew, read, write],
  );

  // 单飞：正在取数时又来通知，只记下要做什么，当前请求结束后再做一次（AC-17）。
  const drain = useCallback(async () => {
    if (running.current || !read()) return;
    running.current = true;
    try {
      while (true) {
        const work = pending.current;
        pending.current = noPending();
        const changed = [...work.changed];
        if (!work.fetchNew && !work.reconcile && !work.older && changed.length === 0) break;
        try {
          if (work.reconcile) await reconcile();
          if (work.fetchNew) await fetchNew();
          if (changed.length > 0) await fetchChanged(changed);
          if (work.older) await latestQuery.current.fetchNextPage();
        } catch {
          // 增量失败不打扰你：下一次轮询或重连会补上。
        }
      }
    } finally {
      running.current = false;
    }
  }, [fetchChanged, fetchNew, read, reconcile]);

  useMessageEvents((event) => {
    if (event.type === "reconnected") pending.current.reconcile = true;
    else if (event.channel_id !== channelId) return;
    else if (event.type === "new_message") pending.current.fetchNew = true;
    else for (const id of event.message_ids) pending.current.changed.add(id);
    void drain();
  });

  useEffect(() => {
    const id = window.setInterval(() => {
      pending.current.reconcile = true;
      void drain();
    }, RECONCILE_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [drain]);

  // 首次加载期间到的通知先记着，数据到了再处理。
  const loaded = query.data !== undefined;
  useEffect(() => {
    if (loaded) void drain();
  }, [loaded, drain]);

  const data = query.data;
  const messages = useMemo(() => (data ? flatten(data).map(toMessageView) : []), [data]);
  const resolver = useMemo<MentionResolver>(() => {
    const users = new Map(data?.pages.flatMap((p) => p.users.map((u) => [u.id, u.name] as const)));
    const channels = new Map(
      data?.pages.flatMap((p) => p.channels.map((c) => [c.id, c.name] as const)),
    );
    return { user: (id) => users.get(id), channel: (id) => channels.get(id) };
  }, [data]);

  const loadOlder = useCallback(() => {
    pending.current.older = true;
    void drain();
  }, [drain]);

  const status = query.isPending
    ? "loading"
    : !data
      ? "error"
      : messages.length === 0
        ? "empty"
        : "ready";

  return {
    messages,
    resolver,
    status,
    error: query.error,
    hasOlder: query.hasNextPage,
    isLoadingOlder: query.isFetchingNextPage,
    olderFailed: query.isFetchNextPageError ? query.error : null,
    loadOlder,
    retry: () => {
      if (data) loadOlder();
      else void query.refetch();
    },
    epoch,
  };
}
