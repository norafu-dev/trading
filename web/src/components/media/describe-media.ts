// 附件存储设置页的文字和数值（spec 0011 AC-8）。纯函数，没有请求和时间。
import type { components } from "@/lib/api/schema";

export type MediaSettings = components["schemas"]["MediaSettingsOut"];

export const MAX_FILE_MB_RANGE = { min: 1, max: 1024 } as const;
export const QUOTA_GB_RANGE = { min: 1, max: 10000 } as const;

const GIB = 1024 ** 3;

export const STORAGE_FULL_TEXT = "附件空间已满";
export const STORAGE_FULL_DETAIL = "空间已满，新附件没有保存";

/** 已用容量，GB 一位小数，按 1024 进位。 */
export function formatUsedGb(usedBytes: number): string {
  return `${(usedBytes / GIB).toFixed(1)} GB`;
}

/** 已用占总容量的整数百分比，最多 100。 */
export function usedPercent(usedBytes: number, quotaGb: number): number {
  const quotaBytes = quotaGb * GIB;
  if (quotaBytes <= 0) return 0;
  return Math.min(100, Math.floor((usedBytes / quotaBytes) * 100));
}

export const COUNT_LABELS: readonly (readonly [keyof MediaSettings["counts"], string])[] = [
  ["pending", "待保存"],
  ["stored", "已保存"],
  ["failed", "失败"],
  ["too_large", "超过上限"],
  ["no_space", "空间不足"],
  ["gone", "已失效"],
];

export type LimitsInput = Readonly<{ maxFileMb: string; quotaGb: string }>;
export type LimitsCheck =
  | Readonly<{ ok: true; maxFileMb: number; quotaGb: number }>
  | Readonly<{ ok: false; maxFileMb?: string; quotaGb?: string }>;

function parseWhole(text: string, min: number, max: number, unit: string): number | string {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) return `请输入 ${min} 到 ${max} 之间的整数（${unit}）`;
  const value = Number(trimmed);
  return value >= min && value <= max ? value : `范围是 ${min} 到 ${max} ${unit}`;
}

/** 两个上限都是整数，超出范围时不保存并说明（AC-8）。 */
export function checkLimits(input: LimitsInput): LimitsCheck {
  const maxFile = parseWhole(input.maxFileMb, MAX_FILE_MB_RANGE.min, MAX_FILE_MB_RANGE.max, "MB");
  const quota = parseWhole(input.quotaGb, QUOTA_GB_RANGE.min, QUOTA_GB_RANGE.max, "GB");
  if (typeof maxFile === "number" && typeof quota === "number") {
    return { ok: true, maxFileMb: maxFile, quotaGb: quota };
  }
  return {
    ok: false,
    maxFileMb: typeof maxFile === "string" ? maxFile : undefined,
    quotaGb: typeof quota === "string" ? quota : undefined,
  };
}
