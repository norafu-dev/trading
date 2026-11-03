import { RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import type { UiError } from "@/lib/api/errors";

type OlderLoaderProps = Readonly<{
  isLoading: boolean;
  error?: UiError;
  onRetry?: () => void;
}>;

// 列表顶部的一行：加载更早消息时转圈，失败时显示一行小字和「重试」。浮在列表上方，
// 不占列表里的位置，所以出现和消失都不会让下面的消息跳动。
export function OlderLoader({ isLoading, error, onRetry }: OlderLoaderProps) {
  if (!isLoading && !error) return null;
  return (
    <div className="pointer-events-none absolute inset-x-0 top-2 z-10 flex justify-center px-4">
      <div
        role="status"
        className="pointer-events-auto flex items-center gap-2 rounded-full border border-border bg-popover px-3 py-1 text-xs text-popover-foreground shadow-sm"
      >
        {error ? (
          <>
            <span>{error.title}，更早的消息没加载出来</span>
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
            <span>正在加载更早的消息</span>
          </>
        )}
      </div>
    </div>
  );
}
