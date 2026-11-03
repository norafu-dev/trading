const UNITS = ["KB", "MB", "GB"] as const;

// 附件大小按 1024 进位：不到 1 KB 显示整数字节，否则保留一位小数。
export function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${Math.max(0, Math.round(sizeBytes))} B`;
  let value = sizeBytes / 1024;
  let unit: (typeof UNITS)[number] = "KB";
  for (const next of UNITS.slice(1)) {
    if (value < 1024) break;
    value /= 1024;
    unit = next;
  }
  return `${value.toFixed(1)} ${unit}`;
}
