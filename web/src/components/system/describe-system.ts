// 系统状态的文字和色调（spec 0009 AC-4、AC-7）。纯函数：时间格式化和当前时间都从参数传入。
import { STALE_TEXT } from "@/components/discord/account-status";

import type { BackupRun, SystemStatusData } from "./use-system-status";

export type Tone = "ok" | "pending" | "error";

export type StatusLine = Readonly<{
  tone: Tone;
  value: string;
  detail?: string;
  /** 悬停时显示的完整时间。 */
  title?: string;
}>;

export type TimeText = Readonly<{
  relative: (iso: string) => string;
  full: (iso: string) => string;
}>;

export const BACKUP_OVERDUE_TEXT = "备份超过 2 天没有成功";

const BACKUP_STEP_TEXT: Readonly<Record<NonNullable<BackupRun["error"]>, string>> = {
  dump: "导出",
  encrypt: "加密",
  upload: "上传",
  interrupted: "被中断",
};

export function formatBackupSize(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

export function describeCollector(
  collector: SystemStatusData["collector"],
  time: TimeText,
): StatusLine {
  if (collector.stale) {
    const at = collector.last_heartbeat_at;
    return at
      ? {
          tone: "error",
          value: STALE_TEXT,
          detail: `最近心跳 ${time.relative(at)}`,
          title: time.full(at),
        }
      : { tone: "error", value: STALE_TEXT, detail: "还没有心跳" };
  }
  const at = collector.last_heartbeat_at;
  if (!at) return { tone: "pending", value: "未运行" };
  return {
    tone: "ok",
    value: "正常",
    detail: `最近心跳 ${time.relative(at)}`,
    title: time.full(at),
  };
}

export function describeBackup(backup: SystemStatusData["backup"], time: TimeText): StatusLine {
  const run = backup.last_run;
  if (!run) return { tone: "pending", value: "还没有备份" };
  const overdue = backup.overdue;
  if (run.status === "running") {
    return {
      tone: overdue ? "error" : "pending",
      value: "备份中",
      detail: `开始于 ${time.relative(run.started_at)}`,
      title: time.full(run.started_at),
    };
  }
  const finished = run.finished_at ?? run.started_at;
  if (run.status === "failed") {
    const step = run.error ? BACKUP_STEP_TEXT[run.error] : "未知步骤";
    return {
      tone: "error",
      value: `失败：${step}`,
      detail: time.relative(finished),
      title: time.full(finished),
    };
  }
  const size = run.size_bytes === null ? "" : ` · ${formatBackupSize(run.size_bytes)}`;
  return {
    tone: overdue ? "error" : "ok",
    value: "成功",
    detail: `${time.relative(finished)}${size}`,
    title: time.full(finished),
  };
}

// 首页「附件下载」一行（spec 0011 AC-22）：心跳的相对时间；任务无响应或暂停时单独说明。
export function describeMediaWorker(media: SystemStatusData["media"], time: TimeText): StatusLine {
  const seen = media.last_seen_at;
  if (media.stale) {
    return seen
      ? {
          tone: "error",
          value: "附件下载任务无响应",
          detail: `最近心跳 ${time.relative(seen)}`,
          title: time.full(seen),
        }
      : { tone: "error", value: "附件下载任务无响应", detail: "还没有心跳" };
  }
  if (media.paused_until) {
    return {
      tone: "pending",
      value: `已暂停到 ${time.full(media.paused_until)}`,
      detail: seen ? `最近心跳 ${time.relative(seen)}` : undefined,
    };
  }
  if (!seen) return { tone: "pending", value: "未运行" };
  return {
    tone: "ok",
    value: "正常",
    detail: `最近心跳 ${time.relative(seen)}`,
    title: time.full(seen),
  };
}
