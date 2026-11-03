import type { SystemStatusData } from "./use-system-status";

// 测试用的系统状态：心跳新鲜、没有备份记录。
export const SYSTEM_STATUS: SystemStatusData = {
  collector: { last_heartbeat_at: "2026-11-01T10:00:00Z", stale: false, has_account: true },
  backup: { last_run: null, last_success_at: null, overdue: false },
};

export function systemStatusResponse(data: SystemStatusData = SYSTEM_STATUS) {
  return { data, response: new Response(null, { status: 200 }) };
}
