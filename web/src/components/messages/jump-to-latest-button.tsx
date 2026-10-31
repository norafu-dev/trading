import { ArrowDownIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

type JumpToLatestButtonProps = Readonly<{ newCount: number; onClick: () => void }>;

// 不在底部时出现在列表右下角；带上这段时间新来的条数。
export function JumpToLatestButton({ newCount, onClick }: JumpToLatestButtonProps) {
  return (
    <Button onClick={onClick} className="absolute right-4 bottom-4 shadow-md">
      <ArrowDownIcon data-icon="inline-start" aria-hidden />
      回到最新
      {newCount > 0 ? (
        <span className="rounded-full bg-primary-foreground px-1.5 text-xs text-primary">
          {newCount > 99 ? "99+" : newCount} 条新消息
        </span>
      ) : null}
    </Button>
  );
}
