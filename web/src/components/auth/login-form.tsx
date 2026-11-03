"use client";

import { useMutation } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api/client";
import { toApiError, toUiError } from "@/lib/api/errors";
import { hardNavigate } from "@/lib/auth/navigate";
import { safeNextPath } from "@/lib/auth/safe-next";

import { AuthCard, FormField } from "./auth-card";
import { FormError } from "./form-error";
import { useAuthPageRedirect } from "./use-auth-page-redirect";

export function LoginForm() {
  useAuthPageRedirect("login");
  const params = useSearchParams();
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");

  const login = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/auth/login", {
        body: { password, totp_code: totpCode },
      });
      if (!response.ok) throw toApiError(response.status, error);
    },
    onSuccess: () => hardNavigate(safeNextPath(params.get("next"))),
    // 验证码每 30 秒就变，留着旧的只会再错一次。
    onError: () => setTotpCode(""),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate();
  }

  return (
    <AuthCard title="登录 Harvester">
      {params.get("reason") === "expired" && !login.isError ? (
        <Alert>
          <AlertDescription>登录已过期，请重新登录</AlertDescription>
        </Alert>
      ) : null}
      {login.isError ? <FormError error={toUiError(login.error)} /> : null}
      <form onSubmit={submit} className="flex flex-col gap-4">
        <FormField id="password" label="密码">
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </FormField>
        <FormField id="totp" label="验证码" hint="验证器 App 里的 6 位数字">
          <Input
            id="totp"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            required
            value={totpCode}
            onChange={(e) => setTotpCode(e.target.value)}
          />
        </FormField>
        <Button type="submit" disabled={login.isPending}>
          {login.isPending ? <Spinner data-icon="inline-start" /> : null}
          登录
        </Button>
      </form>
    </AuthCard>
  );
}
