"use client";

import { useEffect, useState } from "react";

import { initParser } from "./parser";

export type ParserStatus = "pending" | "ready" | "failed";

const TIMEOUT_MS = 3000;

// 懒加载 WASM 解析器（只在用到消息列表的页面调用）。3 秒内没就绪或加载失败就是 "failed"，
// 消息改用纯文本显示，之后即使加载成功也不再切换，避免行高跳动（AC-10）。
export function useMarkdownParser(timeoutMs: number = TIMEOUT_MS): ParserStatus {
  const [status, setStatus] = useState<ParserStatus>("pending");

  useEffect(() => {
    let settled = false;
    const settle = (next: ParserStatus) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      setStatus(next);
    };
    const timer = window.setTimeout(() => settle("failed"), timeoutMs);
    initParser().then(
      () => settle("ready"),
      (error: unknown) => {
        if (!settled) console.error("Discord markdown 解析器加载失败，消息改用纯文本显示", error);
        settle("failed");
      },
    );
    return () => {
      settled = true;
      window.clearTimeout(timer);
    };
  }, [timeoutMs]);

  return status;
}
