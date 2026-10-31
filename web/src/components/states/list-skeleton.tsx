import { Skeleton } from "@/components/ui/skeleton";

// 每行宽度不同，骨架看起来像消息而不是表格。
const LINE_WIDTHS = [
  ["w-24", "w-4/5", "w-3/5"],
  ["w-32", "w-2/3", "w-1/2"],
  ["w-20", "w-11/12", "w-2/5"],
  ["w-28", "w-3/4", "w-1/3"],
] as const;

export function ListSkeleton({ rows = 8 }: Readonly<{ rows?: number }>) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="正在加载消息"
      className="flex flex-col gap-4 px-4 py-4"
    >
      {Array.from({ length: rows }, (_, index) => {
        const [name, first, second] = LINE_WIDTHS[index % LINE_WIDTHS.length] ?? LINE_WIDTHS[0];
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: 静态占位行，顺序不会变
          <div key={index} className="flex gap-4">
            <Skeleton className="size-10 shrink-0 rounded-full" />
            <div className="flex min-w-0 flex-1 flex-col gap-2 pt-1">
              <Skeleton className={`h-3.5 ${name}`} />
              <Skeleton className={`h-3 ${first}`} />
              <Skeleton className={`h-3 ${second}`} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
