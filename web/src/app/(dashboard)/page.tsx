import { MousePointerClickIcon } from "lucide-react";

import { EmptyState } from "@/components/states/empty-state";

import { SystemStatus } from "./system-status";

export default function Home() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-xl flex-col gap-8 px-4 py-12">
        <EmptyState
          icon={MousePointerClickIcon}
          title="从左边选一个频道"
          description="选中的频道会在这里显示采集到的消息。"
        />
        <SystemStatus />
      </div>
    </div>
  );
}
