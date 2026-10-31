"use client";

import { ArrowUpToLineIcon, MessageSquarePlusIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { MessageList } from "@/components/messages/message-list";
import type { MessageListProps, MessageView } from "@/components/messages/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  FAKE_RESOLVER,
  generateMessages,
  generateNewMessage,
  generateOlderBatch,
} from "@/dev/fake-messages";
import { toUiError } from "@/lib/api/errors";
import { useNow } from "@/lib/format/use-now";

// 模拟参数（spec 0003）：初始 5 万条；每次加载更早的插入 500 条，延迟 300ms，20 次之后没有更多。
const INITIAL_COUNT = 50_000;
const OLDER_BATCH = 500;
const OLDER_DELAY_MS = 300;
const MAX_OLDER_BATCHES = 20;
// 固定的「现在」，让假数据每次都一样；时间显示仍然用真实的 now。
const FAKE_END_MS = Date.parse("2026-10-31T06:00:00.000Z");

const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

// 每半秒数一次列表里带 data-message-row 的元素，验证虚拟滚动只渲染了几十行（AC-4）。
function useDomRowCount(container: React.RefObject<HTMLElement | null>): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => {
      setCount(container.current?.querySelectorAll("[data-message-row]").length ?? 0);
    }, 500);
    return () => window.clearInterval(id);
  }, [container]);
  return count;
}

export function MessageListDemo() {
  const now = useNow();
  const containerRef = useRef<HTMLDivElement>(null);
  const domRows = useDomRowCount(containerRef);
  const [messages, setMessages] = useState<readonly MessageView[]>(() =>
    generateMessages(INITIAL_COUNT, FAKE_END_MS, 20261031),
  );
  const [batches, setBatches] = useState(0);
  const [isLoadingOlder, setIsLoadingOlder] = useState(false);
  const [failOlder, setFailOlder] = useState(false);
  const [olderFailed, setOlderFailed] = useState(false);
  const [sent, setSent] = useState(0);

  const hasOlder = batches < MAX_OLDER_BATCHES;

  const loadOlder = useCallback(() => {
    if (isLoadingOlder || !hasOlder) return;
    setIsLoadingOlder(true);
    setOlderFailed(false);
    window.setTimeout(() => {
      if (failOlder) {
        setOlderFailed(true);
      } else {
        setMessages((current) => {
          const oldest = current[0];
          return oldest
            ? [...generateOlderBatch(oldest, OLDER_BATCH, 7000 + batches), ...current]
            : current;
        });
        setBatches((value) => value + 1);
      }
      setIsLoadingOlder(false);
    }, OLDER_DELAY_MS);
  }, [batches, failOlder, hasOlder, isLoadingOlder]);

  const addNewMessage = () => {
    setMessages((current) => {
      const newest = current.at(-1);
      return newest ? [...current, generateNewMessage(newest, Date.now(), sent + 1)] : current;
    });
    setSent((value) => value + 1);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={addNewMessage}>
          <MessageSquarePlusIcon data-icon="inline-start" aria-hidden />
          模拟新消息
        </Button>
        <Button variant="secondary" onClick={loadOlder} disabled={isLoadingOlder || !hasOlder}>
          <ArrowUpToLineIcon data-icon="inline-start" aria-hidden />
          模拟加载更早的消息
        </Button>
        <label className="flex items-center gap-2 px-2 text-sm">
          <input
            type="checkbox"
            checked={failOlder}
            onChange={(event) => setFailOlder(event.target.checked)}
            className="size-4 accent-primary"
          />
          加载更早的消息时失败
        </label>
        <div className="ml-auto flex flex-wrap gap-2">
          <Badge variant="secondary">{messages.length.toLocaleString("zh-CN")} 条消息</Badge>
          <Badge variant={domRows > 100 ? "destructive" : "default"}>DOM 消息行 {domRows}</Badge>
          <Badge variant="outline">
            {hasOlder ? `还能加载 ${MAX_OLDER_BATCHES - batches} 次` : "没有更早的了"}
          </Badge>
        </div>
      </div>
      <div ref={containerRef} className="h-[640px] overflow-hidden rounded-lg border border-border">
        <MessageList
          messages={messages}
          resolver={FAKE_RESOLVER}
          now={now}
          timeZone={TIME_ZONE}
          hasOlder={hasOlder}
          isLoadingOlder={isLoadingOlder}
          olderError={olderFailed ? toUiError(new TypeError("fetch failed")) : undefined}
          onReachTop={loadOlder}
          onRetry={loadOlder}
          status="ready"
        />
      </div>
    </div>
  );
}

const NOOP = () => {};

const STATE_PROPS: Omit<MessageListProps, "status"> = {
  messages: [],
  resolver: FAKE_RESOLVER,
  now: new Date(FAKE_END_MS),
  timeZone: TIME_ZONE,
  hasOlder: false,
  isLoadingOlder: false,
  onReachTop: NOOP,
};

export function MessageListStates() {
  const states: readonly (readonly [string, MessageListProps])[] = [
    ["加载中", { ...STATE_PROPS, status: "loading" }],
    ["没有消息", { ...STATE_PROPS, status: "ready" }],
    [
      "首次加载失败",
      {
        ...STATE_PROPS,
        status: "error",
        error: toUiError(new TypeError("fetch failed")),
        onRetry: NOOP,
      },
    ],
  ];
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {states.map(([label, props]) => (
        <figure key={label} className="flex flex-col gap-2">
          <figcaption className="text-xs font-semibold text-muted-foreground">{label}</figcaption>
          <div className="h-80 overflow-hidden rounded-lg border border-border">
            <MessageList {...props} />
          </div>
        </figure>
      ))}
    </div>
  );
}
