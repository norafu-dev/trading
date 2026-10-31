import { cn } from "cn";

// 色板直接读 CSS 变量，显示的是当前系统主题下的值；改系统外观（或在开发者工具里模拟
// prefers-color-scheme）就能看到另一套。
const SWATCHES: readonly (readonly [string, string, string])[] = [
  ["background", "bg-background", "text-foreground"],
  ["muted", "bg-muted", "text-muted-foreground"],
  ["card", "bg-card", "text-card-foreground"],
  ["primary", "bg-primary", "text-primary-foreground"],
  ["secondary", "bg-secondary", "text-secondary-foreground"],
  ["accent", "bg-accent", "text-accent-foreground"],
  ["destructive", "bg-destructive", "text-destructive-foreground"],
  ["rail", "bg-rail", "text-rail-foreground"],
  ["mention", "bg-mention", "text-foreground"],
  ["warning", "bg-warning", "text-primary-foreground"],
  ["avatar-1", "bg-avatar-1", "text-avatar-foreground"],
  ["avatar-2", "bg-avatar-2", "text-avatar-foreground"],
  ["avatar-3", "bg-avatar-3", "text-avatar-foreground"],
  ["avatar-4", "bg-avatar-4", "text-avatar-foreground"],
  ["avatar-5", "bg-avatar-5", "text-avatar-foreground"],
  ["avatar-6", "bg-avatar-6", "text-avatar-foreground"],
];

export function TokenSwatches() {
  return (
    <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {SWATCHES.map(([name, background, foreground]) => (
        <li
          key={name}
          className={cn(
            "flex h-20 flex-col justify-end rounded-lg border border-border p-3",
            background,
            foreground,
          )}
        >
          <span className="text-xs font-semibold">--{name}</span>
          <span className="text-xs">Aa 文字</span>
        </li>
      ))}
    </ul>
  );
}
