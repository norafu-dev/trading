"use client";

import { MousePointerClickIcon } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ExportBar } from "@/components/export/export-bar";
import { ExportButton } from "@/components/export/export-button";
import {
  type ExportSelection,
  type ExportSide,
  NO_SELECTION,
  selectMessage,
  toggleOpen,
} from "@/components/export/export-selection";
import { MessageList } from "@/components/messages/message-list";
import { channelHref } from "@/components/shell/routes";
import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { ApiResponseError, toUiError } from "@/lib/api/errors";
import { useNow } from "@/lib/format/use-now";

import { ChannelHistoryActions } from "./channel-history-actions";
import { ChannelStatusBanner } from "./channel-status-banner";
import { useSetCurrentChannel } from "./unread-provider";
import { useChannelDetail } from "./use-channel-detail";
import { useChannelMessages } from "./use-channel-messages";
import { useReportRead } from "./use-report-read";

const TIME_ZONE = Intl.DateTimeFormat().resolvedOptions().timeZone;

// `m` 必须是十进制消息 ID；别的写法按「找不到这条消息」处理（AC-18）。
const MESSAGE_ID = /^\d+$/;

function ChannelView({
  channelId,
  messageParam,
}: Readonly<{ channelId: string; messageParam: string | null }>) {
  const router = useRouter();
  const now = useNow();
  const detail = useChannelDetail(channelId);
  const center = messageParam !== null && MESSAGE_ID.test(messageParam) ? messageParam : null;
  const channel = useChannelMessages(channelId, center);
  // 以某条为中心浏览、还没加载到最新时不上报已读（AC-19）。
  useReportRead(
    channelId,
    channel.messages.at(-1)?.id ?? null,
    channel.status === "ready" && !channel.hasNewer,
  );

  // 导出的选择状态放在这里：ChannelView 按频道换 key，切换频道自然清空；
  // MessageList 会在 epoch 或 center 变化时重新挂载，放在它里面会丢（spec 0014 AC-5）。
  const [exportSelection, setExportSelection] = useState<ExportSelection | null>(null);
  const exporting = exportSelection !== null;
  const pickMessage = useCallback(
    (message: Readonly<{ id: string; createdAt: string; authorName: string }>) =>
      setExportSelection((prev) => selectMessage(prev ?? NO_SELECTION, message)),
    [],
  );
  const toggleEnd = useCallback(
    (side: ExportSide) => setExportSelection((prev) => toggleOpen(prev ?? NO_SELECTION, side)),
    [],
  );
  const cancelExport = useCallback(() => setExportSelection(null), []);
  useEffect(() => {
    if (!exporting) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExportSelection(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [exporting]);

  // 找不到这条消息：提示一下，从网址去掉 `m`，按普通方式加载最新一页（AC-18）。
  const [notFound, setNotFound] = useState(false);
  const missing = (messageParam !== null && center === null) || channel.centerMissing;
  useEffect(() => {
    if (!missing) return;
    setNotFound(true);
    router.replace(channelHref(channelId));
  }, [missing, channelId, router]);

  if (detail.error instanceof ApiResponseError && detail.error.status === 404) {
    return <ErrorBlock error={toUiError(detail.error)} className="h-full" />;
  }
  const reason = detail.data?.banner_reason ?? null;
  return (
    <section
      aria-label={detail.data ? `${detail.data.name} 的消息` : "频道消息"}
      className="flex h-full flex-col"
    >
      {reason !== null ? <ChannelStatusBanner reason={reason} /> : null}
      {notFound ? (
        <p role="status" className="border-b border-border bg-muted px-4 py-2 text-sm">
          找不到这条消息，已显示最新消息
        </p>
      ) : null}
      <ChannelHistoryActions
        channelId={channelId}
        channelName={detail.data?.name}
        actions={
          <ExportButton
            channelType={detail.data?.type}
            active={exporting}
            onClick={() => setExportSelection((prev) => (prev === null ? NO_SELECTION : null))}
          />
        }
      />
      <div className="min-h-0 flex-1">
        <MessageList
          key={`${channel.epoch}:${center ?? ""}`}
          messages={channel.messages}
          resolver={channel.resolver}
          now={now}
          timeZone={TIME_ZONE}
          hasOlder={channel.hasOlder}
          isLoadingOlder={channel.isLoadingOlder}
          olderError={channel.olderFailed ? toUiError(channel.olderFailed) : undefined}
          onReachTop={channel.loadOlder}
          status={channel.status}
          error={channel.status === "error" ? toUiError(channel.error) : undefined}
          onRetry={channel.retry}
          centerId={channel.initialCenter}
          hasNewer={channel.hasNewer}
          isLoadingNewer={channel.isLoadingNewer}
          newerError={channel.newerFailed ? toUiError(channel.newerFailed) : undefined}
          onReachBottom={channel.loadNewer}
          onRetryNewer={channel.loadNewer}
          onJumpLatest={() => router.replace(channelHref(channelId))}
          exportSelection={exportSelection}
          onExportPick={pickMessage}
        />
      </div>
      {exportSelection !== null ? (
        <ExportBar
          channelId={channelId}
          selection={exportSelection}
          timeZone={TIME_ZONE}
          onToggleOpen={toggleEnd}
          onCancel={cancelExport}
        />
      ) : null}
    </section>
  );
}

// `/channels?id=<频道ID>`。换频道时列表重新挂载，打开时停在最新处（AC-16）。
export function ChannelPage() {
  const searchParams = useSearchParams();
  const channelId = searchParams.get("id");
  const messageParam = searchParams.get("m");
  const setCurrentChannel = useSetCurrentChannel();

  useEffect(() => {
    setCurrentChannel(channelId);
    return () => setCurrentChannel(null);
  }, [channelId, setCurrentChannel]);

  if (channelId === null) {
    return (
      <EmptyState
        icon={MousePointerClickIcon}
        title="从左边选一个频道"
        description="选中的频道会在这里显示采集到的消息。"
        className="h-full"
      />
    );
  }
  return <ChannelView key={channelId} channelId={channelId} messageParam={messageParam} />;
}
