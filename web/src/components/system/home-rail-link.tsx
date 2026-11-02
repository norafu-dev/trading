"use client";

import { RailHomeLink } from "@/components/shell/server-rail";

import { BACKUP_OVERDUE_TEXT } from "./describe-system";
import { useSystemStatus } from "./use-system-status";

// 图标栏的首页入口：备份逾期时带红点（spec 0009 AC-7）。
export function HomeRailLink() {
  const { data } = useSystemStatus();
  return <RailHomeLink alert={data?.backup.overdue ? BACKUP_OVERDUE_TEXT : undefined} />;
}
