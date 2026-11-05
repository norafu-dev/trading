"use client";

import { XIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { MAX_AUTHORS, MAX_CHANNELS, type SearchFilters, type TimeRange } from "./search-params";
import { type AuthorData, useAuthorSuggestions, usePicker, useSelectedAuthors } from "./use-search";

type Change = (next: Partial<SearchFilters>) => void;
type FilterProps = Readonly<{ filters: SearchFilters; onChange: Change }>;

const toggle = (list: readonly string[], id: string): readonly string[] =>
  list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

// 服务器和频道共用一个选择器：整个服务器勾选发 guild，单个频道或线程发 channel（AC-8）。
export function ChannelPicker({ filters, onChange }: FilterProps) {
  const { data } = usePicker();
  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const needle = text.trim().toLowerCase();
  const count = filters.guilds.length + filters.channels.length;

  return (
    <details className="relative">
      <summary className="flex h-10 cursor-pointer list-none items-center rounded-input border border-input px-3 text-sm">
        服务器和频道{count > 0 ? `（${count}）` : ""}
      </summary>
      <div className="absolute z-20 mt-1 max-h-96 w-80 overflow-y-auto rounded-lg border bg-popover p-2 shadow-lg">
        <Input
          aria-label="搜索服务器和频道"
          placeholder="按名字搜索"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        {notice ? <p className="px-1 pt-1 text-xs text-destructive">{notice}</p> : null}
        {data?.guilds.map((guild) => {
          const channels = guild.channels.filter(
            (c) =>
              !needle ||
              c.name.toLowerCase().includes(needle) ||
              guild.name.toLowerCase().includes(needle),
          );
          if (channels.length === 0) return null;
          return (
            <fieldset key={guild.id} className="mt-2">
              <legend className="sr-only">{guild.name}</legend>
              <label className="flex items-center gap-2 py-1 text-sm font-semibold">
                <input
                  type="checkbox"
                  checked={filters.guilds.includes(guild.id)}
                  onChange={() => onChange({ guilds: toggle(filters.guilds, guild.id) })}
                />
                {guild.name}
                {guild.status !== "active" ? <Badge variant="secondary">已退出</Badge> : null}
              </label>
              {channels.map((c) => {
                const thread = c.parent_id !== null && c.category_name === null && c.type >= 10;
                return (
                  <label
                    key={c.id}
                    className={`flex items-center gap-2 py-0.5 text-sm ${thread ? "pl-10" : "pl-5"}`}
                  >
                    <input
                      type="checkbox"
                      checked={filters.channels.includes(c.id)}
                      onChange={() => {
                        const next = toggle(filters.channels, c.id);
                        if (next.length > MAX_CHANNELS) {
                          setNotice("频道太多，请改选整个服务器");
                          return;
                        }
                        setNotice(null);
                        onChange({ channels: next });
                      }}
                    />
                    <span className="truncate">
                      {c.category_name ? `${c.category_name} / ` : ""}
                      {c.name}
                    </span>
                    {c.status === "deleted" ? (
                      <Badge variant="destructive">已删除</Badge>
                    ) : !c.collect_enabled ? (
                      <Badge variant="secondary">已关闭采集</Badge>
                    ) : null}
                  </label>
                );
              })}
            </fieldset>
          );
        })}
      </div>
    </details>
  );
}

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

// 作者联想：输入 300 毫秒后请求；选中的人显示成可移除的标签（AC-9）。
export function AuthorPicker({ filters, onChange }: FilterProps) {
  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const debounced = useDebounced(text, 300);
  const suggestions = useAuthorSuggestions(debounced);
  const selected = useSelectedAuthors(filters.authors);
  const names = new Map<string, string>(
    (selected.data ?? []).map((a) => [a.id, a.global_name ?? a.username]),
  );

  const add = (author: AuthorData) => {
    if (filters.authors.includes(author.id)) return;
    if (filters.authors.length >= MAX_AUTHORS) {
      setNotice("最多选 20 人");
      return;
    }
    setNotice(null);
    names.set(author.id, author.global_name ?? author.username);
    onChange({ authors: [...filters.authors, author.id] });
    setText("");
  };

  return (
    <div className="relative">
      <div className="flex flex-wrap items-center gap-1.5">
        {filters.authors.map((id) => (
          <Badge key={id} variant="secondary" className="gap-1">
            {names.get(id) ?? id}
            <button
              type="button"
              aria-label={`移除作者 ${names.get(id) ?? id}`}
              onClick={() => onChange({ authors: filters.authors.filter((x) => x !== id) })}
            >
              <XIcon aria-hidden className="size-3" />
            </button>
          </Badge>
        ))}
        <Input
          aria-label="作者"
          placeholder="作者"
          className="w-36"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      </div>
      {notice ? <p className="pt-1 text-xs text-destructive">{notice}</p> : null}
      {debounced.trim() && text.trim() && suggestions.data ? (
        <ul className="absolute z-20 mt-1 max-h-64 w-64 overflow-y-auto rounded-lg border bg-popover p-1 shadow-lg">
          {suggestions.data.length === 0 ? (
            <li className="px-2 py-1 text-sm text-muted-foreground">没有匹配的作者</li>
          ) : (
            suggestions.data.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className="w-full rounded px-2 py-1 text-left text-sm hover:bg-muted"
                  onClick={() => add(a)}
                >
                  {a.global_name ?? a.username}
                  {a.global_name ? (
                    <span className="ml-1 text-muted-foreground">{a.username}</span>
                  ) : null}
                </button>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  );
}

const RANGE_LABELS: Readonly<Record<TimeRange, string>> = {
  today: "今天",
  "7d": "最近 7 天",
  "30d": "最近 30 天",
};

// <input type="datetime-local"> 的值是本地时间；网址和接口用 UTC 的 ISO 字符串。
const toLocalInput = (iso: string | null): string => {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const fromLocalInput = (value: string): string | null => {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
};

export function TimePicker({ filters, onChange }: FilterProps) {
  const custom = filters.from !== null || filters.to !== null;
  const value = custom ? "custom" : (filters.range ?? "");
  const invalid =
    filters.from !== null &&
    filters.to !== null &&
    Date.parse(filters.from) >= Date.parse(filters.to);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        aria-label="时间"
        className="h-10 rounded-input border border-input bg-transparent px-2 text-sm"
        value={value}
        onChange={(e) => {
          const next = e.target.value;
          if (next === "custom") {
            onChange({ range: null, from: new Date().toISOString(), to: null });
          } else {
            onChange({ range: (next || null) as TimeRange | null, from: null, to: null });
          }
        }}
      >
        <option value="">不限时间</option>
        {(Object.keys(RANGE_LABELS) as TimeRange[]).map((r) => (
          <option key={r} value={r}>
            {RANGE_LABELS[r]}
          </option>
        ))}
        <option value="custom">自定义</option>
      </select>
      {custom ? (
        <>
          <Input
            type="datetime-local"
            aria-label="开始时间"
            className="w-48"
            value={toLocalInput(filters.from)}
            onChange={(e) => onChange({ from: fromLocalInput(e.target.value) })}
          />
          <span aria-hidden>至</span>
          <Input
            type="datetime-local"
            aria-label="结束时间"
            className="w-48"
            value={toLocalInput(filters.to)}
            onChange={(e) => onChange({ to: fromLocalInput(e.target.value) })}
          />
          {invalid ? (
            <p role="alert" className="text-xs text-destructive">
              开始时间要早于结束时间
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function ClearFilters({ onClear }: Readonly<{ onClear: () => void }>) {
  return (
    <Button variant="ghost" size="sm" onClick={onClear}>
      清除筛选
    </Button>
  );
}
