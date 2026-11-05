"use client";

import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toMessageView } from "@/components/messages/to-message-view";
import type { MentionResolver, MessageView } from "@/components/messages/types";
import { api } from "@/lib/api/client";
import { ApiResponseError, toApiError } from "@/lib/api/errors";
import { useMessageEvents } from "@/lib/events/event-stream";

import {
  aroundFrom,
  chunk,
  flatten,
  hasGap,
  hasNewerPages,
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
// 补采每抓一页就发一次通知，合并成 5 秒一次重新取数（spec 0012 AC-17）。
export const HISTORY_COALESCE_MS = 5_000;

type Query = Readonly<{
  before?: string;
  after?: string;
  ids?: string;
  around?: string;
  limit?: number;
}>;

function queryFor(param: PageParam): Query {
  if (param === null) return { limit: PAGE_SIZE };
  if (typeof param === "string") return { before: param, limit: PAGE_SIZE };
  if ("around" in param) return { around: param.around };
  return { after: param.after, limit: PAGE_SIZE };
}

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
  /** 以某条为中心浏览时，向下加载更新的一页（spec 0013 AC-19）。 */
  newer: boolean;
  /** 补采写进来了：重新取已加载的范围，新消息插到正确位置（spec 0012 AC-17）。 */
  refreshLoaded: boolean;
  changed: Set<string>;
};

const noPending = (): Pending => ({
  fetchNew: false,
  reconcile: false,
  older: false,
  newer: false,
  refreshLoaded: false,
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
  /** 以某条为中心浏览、还没加载到最新消息（AC-19）。 */
  hasNewer: boolean;
  isLoadingNewer: boolean;
  newerFailed: unknown;
  loadNewer: () => void;
  /** 中心消息不在这个频道里，或者不存在（AC-18）。 */
  centerMissing: boolean;
  /** 列表第一次定位时要居中的消息；列表被整页替换之后不再有。 */
  initialCenter: string | null;
  retry: () => void;
  /** 列表被整页替换时加一；用作 key 重新挂载，列表回到底部。 */
  epoch: number;
}>;

// `centerId` 有值时以这条消息为中心加载（它前后各 25 条），没有就加载最新一页。
// 换中心就是换 queryKey，重新加载。
export function useChannelMessages(
  channelId: string,
  centerId: string | null = null,
): ChannelMessages {
  const queryClient = useQueryClient();
  const queryKey = useMemo(
    () => ["discord-messages", channelId, centerId] as const,
    [channelId, centerId],
  );
  const [epoch, setEpoch] = useState(0);

  const query = useInfiniteQuery<
    MessagesPage,
    Error,
    MessagesData,
    readonly [string, string, string | null],
    PageParam
  >({
    queryKey,
    initialPageParam: centerId === null ? null : { around: centerId },
    queryFn: ({ pageParam }) => fetchPage(channelId, queryFor(pageParam)),
    getNextPageParam: (last) => (last.has_more ? (last.messages[0]?.id ?? null) : null),
    // 向新翻页：只有最新的一页后面还有更新的消息时才有下一页（pages[0] 在列表最下面）。
    getPreviousPageParam: (first, _all, firstParam) => {
      const newerRemains =
        typeof firstParam === "object" && firstParam !== null
          ? "around" in firstParam
            ? first.has_newer === true
            : first.has_more
          : false;
      const newest = first.messages.at(-1)?.id;
      return newerRemains && newest ? { after: newest } : undefined;
    },
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
  // 以某条为中心浏览、还没加载到最新消息时，不追加实时消息，也不做缺口检查（AC-19）。
  const isAround = useCallback(() => {
    const data = read();
    return data !== undefined && hasNewerPages(data);
  }, [read]);

  const reconcile = useCallback(async () => {
    if (isAround()) return;
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
  }, [channelId, isAround, queryClient, queryKey, read, write]);

  const fetchNew = useCallback(async () => {
    if (isAround()) return;
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
  }, [channelId, isAround, read, reconcile, write]);

  // 补采写进来后，以当前的中心重新取一次，替换已加载的内容（AC-19）。
  const refreshAround = useCallback(async () => {
    if (centerId === null) return;
    const page = await fetchPage(channelId, { around: centerId });
    queryClient.setQueryData<MessagesData>(queryKey, aroundFrom(page, centerId));
  }, [centerId, channelId, queryClient, queryKey]);

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
        if (
          !work.fetchNew &&
          !work.reconcile &&
          !work.older &&
          !work.newer &&
          !work.refreshLoaded &&
          changed.length === 0
        )
          break;
        try {
          if (work.reconcile) await reconcile();
          if (work.fetchNew) await fetchNew();
          if (changed.length > 0) await fetchChanged(changed);
          // 已加载的每一页都按原来的位置重新取，补进来的旧消息就会出现在该在的地方。
          // 带 around 或 after 参数的页不能整体 refetch（页的顺序和游标对不上），改成按中心重新取。
          if (work.refreshLoaded) {
            const loaded = read();
            const mixed = loaded?.pageParams.some((p) => typeof p === "object" && p !== null);
            if (mixed) await refreshAround();
            else await latestQuery.current.refetch();
          }
          if (work.older) await latestQuery.current.fetchNextPage();
          if (work.newer) await latestQuery.current.fetchPreviousPage();
        } catch {
          // 增量失败不打扰你：下一次轮询或重连会补上。
        }
      }
    } finally {
      running.current = false;
    }
  }, [fetchChanged, fetchNew, read, reconcile, refreshAround]);

  // 补采通知很密（每页一次），先攒 5 秒再重新取一次（AC-17）。
  const historyTimer = useRef<number | null>(null);
  const scheduleHistoryRefresh = useCallback(() => {
    if (historyTimer.current !== null) return;
    historyTimer.current = window.setTimeout(() => {
      historyTimer.current = null;
      pending.current.refreshLoaded = true;
      void drain();
    }, HISTORY_COALESCE_MS);
  }, [drain]);

  useEffect(
    () => () => {
      if (historyTimer.current !== null) window.clearTimeout(historyTimer.current);
    },
    [],
  );

  useMessageEvents((event) => {
    if (event.type === "reconnected") pending.current.reconcile = true;
    else if (event.channel_id !== channelId) return;
    else if (event.type === "new_message") pending.current.fetchNew = true;
    else if (event.type === "history_changed") {
      scheduleHistoryRefresh();
      return;
    } else for (const id of event.message_ids) pending.current.changed.add(id);
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

  const loadNewer = useCallback(() => {
    pending.current.newer = true;
    void drain();
  }, [drain]);

  const centerMissing =
    centerId !== null &&
    !data &&
    query.error instanceof ApiResponseError &&
    query.error.code === "message_not_found";
  const hasNewer = data ? hasNewerPages(data) : false;

  const status =
    query.isPending || centerMissing
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
    hasNewer,
    isLoadingNewer: query.isFetchingPreviousPage,
    newerFailed: query.isFetchPreviousPageError ? query.error : null,
    loadNewer,
    centerMissing,
    initialCenter: epoch === 0 ? centerId : null,
    retry: () => {
      if (data) loadOlder();
      else void query.refetch();
    },
    epoch,
  };
}
