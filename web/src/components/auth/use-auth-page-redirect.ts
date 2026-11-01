"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuthState } from "@/lib/auth/use-auth-state";

// /login 和 /setup 共用：已登录去首页，走错页面的去另一个（AC-13）。
export function useAuthPageRedirect(page: "login" | "setup") {
  const router = useRouter();
  const { data: state } = useAuthState();

  useEffect(() => {
    if (state === "authenticated") router.replace("/");
    if (page === "login" && state === "setup_required") router.replace("/setup");
    if (page === "setup" && state === "unauthenticated") router.replace("/login");
  }, [state, page, router]);
}
