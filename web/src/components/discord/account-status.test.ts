import { describe, expect, it } from "vitest";

import { describeAccount } from "./account-status";
import type { DiscordAccount } from "./use-discord-account";

const BASE: DiscordAccount = {
  id: 1,
  label: "小号",
  discord_user_id: null,
  discord_username: null,
  status: "connecting",
  status_changed_at: "2026-11-01T10:00:00Z",
  last_error: null,
  last_heartbeat_at: null,
  retry_at: null,
  collector_stale: false,
};

const format = (iso: string) => `<${iso}>`;

function describeWith(overrides: Partial<DiscordAccount>) {
  return describeAccount({ ...BASE, ...overrides }, format);
}

describe("describeAccount", () => {
  // covers: AC-7
  it("login_paused 显示格式化后的恢复时间", () => {
    const info = describeWith({ status: "login_paused", retry_at: "2026-11-02T10:00:00Z" });

    expect(info.text).toBe("已暂停登录，将在 <2026-11-02T10:00:00Z> 恢复");
  });

  it("login_paused 没有 retry_at 时不拼出空时间", () => {
    expect(describeWith({ status: "login_paused" }).text).toBe("已暂停登录");
  });

  it("只有 token_undecryptable 才换成解密失败的文案", () => {
    expect(describeWith({ status: "token_invalid", last_error: "http_401" }).text).toBe(
      "token 失效或账号被封，请到 Discord 确认",
    );
    expect(describeWith({ status: "token_invalid", last_error: "token_undecryptable" }).text).toBe(
      "token 无法解密，请检查主密钥或重新填写",
    );
  });

  it("其他状态带上 token_undecryptable 也不改文案", () => {
    expect(describeWith({ status: "online", last_error: "token_undecryptable" }).text).toBe(
      "在线（隐身）",
    );
  });

  it("换文案时保留原来的操作和色调", () => {
    const info = describeWith({ status: "token_invalid", last_error: "token_undecryptable" });

    expect(info.tone).toBe("error");
    expect(info.operations).toEqual(["replace-token", "reconnect", "disable"]);
  });

  it.each([
    ["online", "ok"],
    ["connecting", "pending"],
    ["reconnecting", "pending"],
    ["login_paused", "pending"],
    ["disabled", "pending"],
    ["token_invalid", "error"],
    ["verification_required", "error"],
    ["account_mismatch", "error"],
    ["banned", "error"],
  ] as const)("%s 的色调是 %s", (status, tone) => {
    expect(describeWith({ status }).tone).toBe(tone);
  });

  // covers: AC-8, AC-11
  it.each([
    ["connecting", "reconnect"],
    ["online", "reconnect"],
    ["banned", "reconnect"],
    ["online", "switch-identity"],
    ["token_invalid", "switch-identity"],
    ["disabled", "disable"],
    ["online", "enable"],
  ] as const)("%s 不提供 %s 操作", (status, operation) => {
    expect(describeWith({ status }).operations).not.toContain(operation);
  });
});
