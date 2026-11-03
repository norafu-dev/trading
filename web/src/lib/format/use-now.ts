"use client";

import { useEffect, useState } from "react";

const TICK_MS = 60_000;

// 页面级的时钟：每分钟更新一次，过了午夜「今天」和「昨天」会自己更正。
// 页面调用一次，把 `now` 往下传；格式化函数从不自己读时钟。
export function useNow(): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), TICK_MS);
    return () => window.clearInterval(id);
  }, []);
  return now;
}
