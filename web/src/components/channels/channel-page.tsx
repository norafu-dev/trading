"use client";

import { MousePointerClickIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { MessageList } from "@/components/messages/message-list";
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

function ChannelView({ channelId }: Readonly<{ channelId: string }>) {
  const now = useNow();
  const detail = useChannelDetail(channelId);
  const channel = useChannelMessages(channelId);
  useReportRead(channelId, channel.messages.at(-1)?.id ?? null, channel.status === "ready");

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
      <ChannelHistoryActions channelId={channelId} channelName={detail.data?.name} />
      <div className="min-h-0 flex-1">
        <MessageList
          key={channel.epoch}
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
        />
      </div>
    </section>
  );
}

// `/channels?id=<频道ID>`。换频道时列表重新挂载，打开时停在最新处（AC-16）。
export function ChannelPage() {
  const channelId = useSearchParams().get("id");
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
  return <ChannelView key={channelId} channelId={channelId} />;
}
