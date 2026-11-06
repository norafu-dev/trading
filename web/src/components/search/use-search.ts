"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";

import { resolveRange, type SearchFilters } from "./search-params";

export type SearchPageData = components["schemas"]["SearchMessagesOut"];
export type SearchHitData = components["schemas"]["SearchHitOut"];
export type PickerData = components["schemas"]["PickerOut"];
export type AuthorData = components["schemas"]["AuthorOut"];

export const STATUS_POLL_MS = 30_000;
const AUTHOR_DEBOUNCE_MIN = 1;

type ApiQuery = Readonly<{
  q?: string;
  guild?: string;
  channel?: string;
  author?: string;
  from?: string;
  to?: string;
}>;

// 把条件换算成接口参数。快捷时间在这里换算成固定时间，所以调用方要把结果放进 queryKey，
// 同一次翻页过程中范围不变（AC-15）。
export function toApiQuery(
  filters: SearchFilters,
  range: ReturnType<typeof resolveRange>,
): ApiQuery {
  return {
    ...(filters.q ? { q: filters.q } : {}),
    ...(filters.guilds.length ? { guild: filters.guilds.join(",") } : {}),
    ...(filters.channels.length ? { channel: filters.channels.join(",") } : {}),
    ...(filters.authors.length ? { author: filters.authors.join(",") } : {}),
    ...(range.from ? { from: range.from } : {}),
    ...(range.to ? { to: range.to } : {}),
  };
}

/** `refreshToken` 变化时重新换算相对时间并重置查询（「刷新」按钮）。 */
export function useResolvedQuery(filters: SearchFilters, refreshToken: number): ApiQuery {
  // biome-ignore lint/correctness/useExhaustiveDependencies: 只在条件或刷新变化时换算，不随时间变
  return useMemo(
    () => toApiQuery(filters, resolveRange(filters, new Date())),
    [
      filters.q,
      filters.guilds,
      filters.channels,
      filters.authors,
      filters.range,
      filters.from,
      filters.to,
      refreshToken,
    ],
  );
}

// `refreshToken` 放进查询键：条件没变（没有相对时间）时，「刷新」也要强制从第一页重取（AC-15）。
export function useSearchResults(query: ApiQuery, enabled: boolean, refreshToken: number) {
  return useInfiniteQuery({
    queryKey: ["search", "messages", query, refreshToken],
    enabled,
    // 快照：不随窗口聚焦或时间重新取，只在「刷新」或条件变化时取（AC-15）。
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: false,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const { data, error, response } = await api.GET("/api/search/messages", {
        params: { query: { ...query, ...(pageParam ? { before: pageParam } : {}) } },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    getNextPageParam: (last) => last.next_before ?? undefined,
  });
}

export function useSearchCount(query: ApiQuery, enabled: boolean, refreshToken: number) {
  return useQuery({
    queryKey: ["search", "count", query, refreshToken],
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: false,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/search/count", {
        params: { query },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
  });
}

export function useIndexStatus() {
  return useQuery({
    queryKey: ["search", "status"],
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/search/status");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    // 补建完成（剩 0 条）后停止刷新（AC-23）。
    refetchInterval: (q) => (q.state.data?.index_pending === 0 ? false : STATUS_POLL_MS),
  });
}

export function usePicker() {
  return useQuery({
    queryKey: ["search", "picker"],
    staleTime: 60_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/search/channels");
      if (!data) throw toApiError(response.status, error);
      return data;
    },
  });
}

export function useAuthorSuggestions(text: string) {
  const trimmed = text.trim();
  return useQuery({
    queryKey: ["search", "authors", "q", trimmed],
    enabled: trimmed.length >= AUTHOR_DEBOUNCE_MIN,
    staleTime: 30_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/search/authors", {
        params: { query: { q: trimmed } },
      });
      if (!data) throw toApiError(response.status, error);
      return data.authors;
    },
  });
}

/** 已选作者的名字：刷新后用网址里的 ID 还原，库里找不到的 ID 不返回。 */
export function useSelectedAuthors(ids: readonly string[]) {
  const key = ids.join(",");
  return useQuery({
    queryKey: ["search", "authors", "ids", key],
    enabled: ids.length > 0,
    staleTime: 60_000,
    queryFn: async () => {
      const { data, error, response } = await api.GET("/api/search/authors", {
        params: { query: { ids: key } },
      });
      if (!data) throw toApiError(response.status, error);
      return data.authors;
    },
  });
}
