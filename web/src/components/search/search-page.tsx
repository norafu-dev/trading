"use client";

import { RotateCwIcon, SearchIcon } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { ListSkeleton } from "@/components/states/list-skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toUiError } from "@/lib/api/errors";
import { useMarkdownParser } from "@/lib/discord-markdown/use-markdown-parser";
import { createTimeFormat } from "@/lib/format/time";
import { useNow } from "@/lib/format/use-now";

import { parseQuery, REASON_TEXT } from "./parse-query";
import { AuthorPicker, ChannelPicker, ClearFilters, TimePicker } from "./search-filters";
import { SearchHit } from "./search-hit";
import {
  EMPTY_FILTERS,
  hasFilterConditions,
  parseSearchParams,
  type SearchFilters,
  toSearchParams,
} from "./search-params";
import { useIndexStatus, useResolvedQuery, useSearchCount, useSearchResults } from "./use-search";

const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

function IndexBanner() {
  const { data } = useIndexStatus();
  if (!data || data.index_pending === 0) return null;
  return (
    <p role="status" className="bg-muted px-4 py-2 text-sm">
      搜索索引补建中，结果可能不全（还剩 {data.index_pending} 条）
    </p>
  );
}

// 滚到底部附近时加载下一页（AC-12）。
function useLoadMore(onLoad: () => void, enabled: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || !enabled) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) onLoad();
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [onLoad, enabled]);
  return ref;
}

export function SearchPage() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(
    () => parseSearchParams(new URLSearchParams(params.toString())),
    [params],
  );
  const [draft, setDraft] = useState(filters.q);
  const [refreshToken, setRefreshToken] = useState(0);
  const now = useNow();
  const parser = useMarkdownParser();
  const format = useMemo(() => createTimeFormat(TIME_ZONE), []);

  // 前进后退改了网址里的关键词时，输入框跟着变。
  useEffect(() => setDraft(filters.q), [filters.q]);

  const parsed = parseQuery(draft);
  const draftError = parsed.ok ? null : REASON_TEXT[parsed.reason];
  const urlParsed = parseQuery(filters.q);
  const excludeOnly =
    urlParsed.ok &&
    urlParsed.include.length === 0 &&
    urlParsed.exclude.length > 0 &&
    !hasFilterConditions(filters);
  const hasAny = filters.q.trim() !== "" || hasFilterConditions(filters);
  const valid = hasAny && urlParsed.ok && !excludeOnly;

  const go = useCallback(
    (next: SearchFilters) => {
      const query = toSearchParams(next).toString();
      router.replace(query ? `${pathname}?${query}` : pathname);
    },
    [router, pathname],
  );
  const change = (patch: Partial<SearchFilters>) => go({ ...filters, ...patch });

  const apiQuery = useResolvedQuery(filters, refreshToken);
  const results = useSearchResults(apiQuery, valid);
  const count = useSearchCount(apiQuery, valid);
  const hits = results.data?.pages.flatMap((p) => p.items) ?? [];
  const resolver = useMemo(() => {
    const users = new Map(
      results.data?.pages.flatMap((p) => p.users.map((u) => [u.id, u.name] as const)),
    );
    const channels = new Map(
      results.data?.pages.flatMap((p) => p.channels.map((c) => [c.id, c.name] as const)),
    );
    return { user: (id: string) => users.get(id), channel: (id: string) => channels.get(id) };
  }, [results.data]);

  const { fetchNextPage, hasNextPage, isFetchingNextPage, isFetchNextPageError } = results;
  const loadMore = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  const sentinel = useLoadMore(
    loadMore,
    Boolean(hasNextPage) && !isFetchingNextPage && !isFetchNextPageError,
  );

  const submit = () => {
    if (!parsed.ok) return;
    change({ q: draft.trim() });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <IndexBanner />
      <search className="border-b">
        <form
          className="flex flex-col gap-2 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <div className="flex gap-2">
            <Input
              aria-label="关键词"
              aria-invalid={draftError ? true : undefined}
              aria-describedby={draftError ? "search-error" : undefined}
              placeholder="输入关键词，空格分隔多个词，-排除，“引号”整体匹配"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button type="submit" disabled={!parsed.ok}>
              <SearchIcon data-icon="inline-start" aria-hidden />
              搜索
            </Button>
          </div>
          {draftError ? (
            <p id="search-error" role="alert" className="text-sm text-destructive">
              {draftError}
            </p>
          ) : null}
          <div className="flex flex-wrap items-start gap-2">
            <ChannelPicker filters={filters} onChange={change} />
            <AuthorPicker filters={filters} onChange={change} />
            <TimePicker filters={filters} onChange={change} />
            {hasAny ? <ClearFilters onClear={() => go(EMPTY_FILTERS)} /> : null}
          </div>
        </form>
      </search>

      {valid ? (
        <div className="flex items-center gap-3 border-b px-4 py-2 text-sm text-muted-foreground">
          <span aria-live="polite">
            {count.data?.count != null
              ? count.data.capped
                ? `${count.data.count}+ 条`
                : `${count.data.count} 条`
              : ""}
          </span>
          <Button variant="ghost" size="sm" onClick={() => setRefreshToken((n) => n + 1)}>
            <RotateCwIcon data-icon="inline-start" aria-hidden />
            刷新
          </Button>
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!hasAny ? (
          <EmptyState icon={SearchIcon} title="输入关键词或选择筛选条件" />
        ) : excludeOnly ? (
          <EmptyState icon={SearchIcon} title={REASON_TEXT.exclude_only} />
        ) : !urlParsed.ok ? (
          <EmptyState icon={SearchIcon} title={REASON_TEXT[urlParsed.reason]} />
        ) : results.isPending ? (
          <ListSkeleton />
        ) : results.isError && !results.data ? (
          <ErrorBlock
            error={toUiError(results.error)}
            onRetry={() => void results.refetch()}
            className="h-full"
          />
        ) : hits.length === 0 ? (
          <EmptyState icon={SearchIcon} title="没有找到符合条件的消息" />
        ) : (
          <>
            <ul>
              {hits.map((hit) => (
                <SearchHit
                  key={hit.message.id}
                  hit={hit}
                  format={format}
                  now={now}
                  resolver={resolver}
                  parser={parser}
                />
              ))}
            </ul>
            <div ref={sentinel} className="h-px" />
            {isFetchingNextPage ? <ListSkeleton rows={2} /> : null}
            {isFetchNextPageError ? (
              <div className="p-4">
                <ErrorBlock error={toUiError(results.error)} onRetry={() => void fetchNextPage()} />
              </div>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
