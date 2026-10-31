import { CloudAlertIcon, RotateCwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import type { UiError } from "@/lib/api/errors";

type ErrorBlockProps = Readonly<{ error: UiError; onRetry?: () => void; className?: string }>;

// 首次加载失败时，占据整个列表或页面的位置。
export function ErrorBlock({ error, onRetry, className }: ErrorBlockProps) {
  return (
    <Empty role="alert" className={className}>
      <EmptyHeader>
        <EmptyMedia variant="icon" className="bg-destructive text-destructive-foreground">
          <CloudAlertIcon aria-hidden strokeWidth={1.75} />
        </EmptyMedia>
        <EmptyTitle className="text-xl font-semibold">{error.title}</EmptyTitle>
        <EmptyDescription>{error.detail}</EmptyDescription>
      </EmptyHeader>
      {onRetry && error.retryable ? (
        <EmptyContent>
          <Button variant="secondary" onClick={onRetry}>
            <RotateCwIcon data-icon="inline-start" aria-hidden />
            重试
          </Button>
        </EmptyContent>
      ) : null}
    </Empty>
  );
}
