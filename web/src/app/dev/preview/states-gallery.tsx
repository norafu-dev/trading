"use client";

import { MousePointerClickIcon, RadioTowerIcon } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/states/empty-state";
import { ErrorBlock } from "@/components/states/error-block";
import { Button } from "@/components/ui/button";
import { ApiResponseError, toUiError } from "@/lib/api/errors";

const NOOP = () => {};

export function StatesGallery() {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-border">
          <EmptyState icon={MousePointerClickIcon} title="从左边选一个频道" />
        </div>
        <div className="rounded-lg border border-border">
          <EmptyState
            icon={RadioTowerIcon}
            title="还没有开启采集的频道"
            description="打开频道的采集开关后，它会出现在这里。"
          />
        </div>
        <div className="rounded-lg border border-border">
          <ErrorBlock error={toUiError(new ApiResponseError(503))} onRetry={NOOP} />
        </div>
        <div className="rounded-lg border border-border">
          <ErrorBlock error={toUiError(new ApiResponseError(401))} />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => toast.success("已保存")}>
          弹出成功提示
        </Button>
        <Button variant="secondary" onClick={() => toast.error("删除失败，请重试")}>
          弹出错误提示
        </Button>
      </div>
    </div>
  );
}
