import { RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { UiError } from "@/lib/api/errors";

type NewerLoaderProps = Readonly<{
  isLoading: boolean;
  error?: UiError;
  onRetry?: () => void;
}>;

// 列表底部的一行：以某条消息为中心浏览时，向下加载更新的消息转圈，失败时显示一行小字和「重试」。
// 和 OlderLoader 一样浮在列表上方，不占列表里的位置。
export function NewerLoader({ isLoading, error, onRetry }: NewerLoaderProps) {
  if (!isLoading && !error) return null;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-2 z-10 flex justify-center px-4">
      <div
        role="status"
        className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-popover px-3 py-1 text-xs text-popover-foreground shadow-sm"
      >
        {error ? (
          <>
            <span>{error.title}，更新的消息没加载出来</span>
            {onRetry ? (
              <Button size="xs" variant="secondary" onClick={onRetry}>
                <RotateCwIcon data-icon="inline-start" aria-hidden />
                重试
              </Button>
            ) : null}
          </>
        ) : (
          <>
            <Spinner />
            <span>正在加载更新的消息</span>
          </>
        )}
      </div>
    </div>
  );
}
