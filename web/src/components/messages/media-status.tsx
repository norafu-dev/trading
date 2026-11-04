import type { MediaStatus } from "./types";

// 没有保存成功的附件显示的文字（spec 0011 AC-15）。已保存的不用文字。
export const MEDIA_STATUS_TEXT: Readonly<Record<Exclude<MediaStatus, "stored">, string>> = {
  pending: "保存中",
  too_large: "超过上限，未保存",
  no_space: "空间不足，未保存",
  failed: "保存失败",
  gone: "Discord 上已失效",
};

export function MediaStatusText({ status }: Readonly<{ status: Exclude<MediaStatus, "stored"> }>) {
  return (
    <span
      className={
        status === "failed" || status === "gone"
          ? "shrink-0 text-destructive"
          : "shrink-0 text-muted-foreground"
      }
    >
      {MEDIA_STATUS_TEXT[status]}
    </span>
  );
}
