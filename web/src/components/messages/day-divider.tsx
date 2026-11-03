import { formatDayDivider } from "@/lib/format/time";

// 跨越本地日期时插入的分隔线，例如「2026年10月30日」。
export function DayDivider({ dayKey }: Readonly<{ dayKey: string }>) {
  const label = formatDayDivider(dayKey);
  return (
    <div className="flex items-center gap-3 px-4 pt-6 pb-2">
      <span aria-hidden className="h-px flex-1 bg-border" />
      <span className="text-xs font-semibold text-muted-foreground">{label}</span>
      <span aria-hidden className="h-px flex-1 bg-border" />
    </div>
  );
}
