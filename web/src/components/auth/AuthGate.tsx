"use client";

import { useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { ErrorBlock } from "@/components/states/error-block";
import { Spinner } from "@/components/ui/spinner";
import { currentPath } from "@/lib/auth/navigate";
import { loginHref } from "@/lib/auth/safe-next";
import { useAuthState } from "@/lib/auth/use-auth-state";

const CANNOT_CONNECT = {
  kind: "network",
  title: "无法连接服务器",
  detail: "检查网络，或者确认服务正在运行。",
  retryable: true,
} as const;

export function FullScreenSpinner() {
  return (
    <div className="flex h-dvh items-center justify-center">
      <Spinner className="size-6" />
    </div>
  );
}

// 静态导出没法在服务端拦截，所以由它守门：确认已登录之前，外壳和数据都不渲染（AC-13）。
export function AuthGate({ children }: Readonly<{ children: ReactNode }>) {
  const router = useRouter();
  const { data: state, isError, isFetching, refetch } = useAuthState();

  useEffect(() => {
    if (state === "setup_required") router.replace("/setup");
    if (state === "unauthenticated") router.replace(loginHref(currentPath()));
  }, [state, router]);

  if (state === "authenticated") return <>{children}</>;
  if (isError && !isFetching) {
    return (
      <div className="flex h-dvh items-center justify-center">
        <ErrorBlock error={CANNOT_CONNECT} onRetry={() => void refetch()} />
      </div>
    );
  }
  return <FullScreenSpinner />;
}
