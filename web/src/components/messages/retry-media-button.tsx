"use client";

import { Button } from "@/components/ui/button";

import { MEDIA_STATUS_TEXT } from "./media-status";
import { useRetryMedia } from "./use-retry-media";

// 「重试」按钮：点击后乐观地改成「保存中」，失败时恢复按钮并提示。
export function RetryMediaButton({ mediaId }: Readonly<{ mediaId: string }>) {
  const retry = useRetryMedia();
  if (retry.isPending || retry.isSuccess) {
    return <span className="shrink-0 text-muted-foreground">{MEDIA_STATUS_TEXT.pending}</span>;
  }
  return (
    <Button type="button" variant="outline" size="xs" onClick={() => retry.mutate(mediaId)}>
      重试
    </Button>
  );
}
