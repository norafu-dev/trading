import type { AccountAction, DiscordAccount } from "./use-discord-account";

export type AccountTone = "ok" | "pending" | "error";
export type AccountOperation = AccountAction | "replace-token";

type StatusInfo = Readonly<{
  text: string;
  tone: AccountTone;
  operations: readonly AccountOperation[];
}>;

const STATUS_INFO: Readonly<Record<DiscordAccount["status"], StatusInfo>> = {
  connecting: { text: "正在连接", tone: "pending", operations: ["disable"] },
  online: { text: "在线（隐身）", tone: "ok", operations: ["replace-token", "disable"] },
  reconnecting: {
    text: "连接中断，正在重试",
    tone: "pending",
    operations: ["replace-token", "disable"],
  },
  login_paused: {
    text: "已暂停登录",
    tone: "pending",
    operations: ["replace-token", "disable"],
  },
  token_invalid: {
    text: "token 失效或账号被封，请到 Discord 确认",
    tone: "error",
    operations: ["replace-token", "reconnect", "disable"],
  },
  verification_required: {
    text: "Discord 要求验证，请用官方客户端处理",
    tone: "error",
    operations: ["reconnect", "replace-token", "disable"],
  },
  account_mismatch: {
    text: "这个 token 属于另一个 Discord 账号",
    tone: "error",
    operations: ["switch-identity", "replace-token", "disable"],
  },
  banned: { text: "账号已被封", tone: "error", operations: ["replace-token", "disable"] },
  disabled: { text: "已停用", tone: "pending", operations: ["enable", "replace-token"] },
};

export const STALE_TEXT = "采集进程无响应";

export function describeAccount(
  account: DiscordAccount,
  formatTime: (iso: string) => string,
): StatusInfo {
  const info = STATUS_INFO[account.status];
  if (account.status === "login_paused" && account.retry_at) {
    return { ...info, text: `已暂停登录，将在 ${formatTime(account.retry_at)} 恢复` };
  }
  if (account.status === "token_invalid" && account.last_error === "token_undecryptable") {
    return { ...info, text: "token 无法解密，请检查主密钥或重新填写" };
  }
  return info;
}
