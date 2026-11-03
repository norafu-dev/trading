"use client";

import { useMutation } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { type FormEvent, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { api } from "@/lib/api/client";
import { ApiResponseError, toApiError, toUiError } from "@/lib/api/errors";
import { hardNavigate } from "@/lib/auth/navigate";

import { AuthCard, FormField } from "./auth-card";
import { FormError } from "./form-error";
import { passwordProblem } from "./password-rules";
import { useAuthPageRedirect } from "./use-auth-page-redirect";

type Step = "code" | "password" | "scan" | "confirm";
type Provisioning = Readonly<{ otpauth_uri: string; secret: string }>;

const STEP_TITLES: Readonly<Record<Step, string>> = {
  code: "第 1 步：输入设置码",
  password: "第 2 步：设置密码",
  scan: "第 3 步：添加到验证器",
  confirm: "第 4 步：确认验证码",
};

// 四步里只有最后一步才保存凭据，中途离开什么都不会留下（AC-4）。
export function SetupWizard() {
  useAuthPageRedirect("setup");
  const [step, setStep] = useState<Step>("code");
  const [setupCode, setSetupCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [totpCode, setTotpCode] = useState("");
  const [provisioning, setProvisioning] = useState<Provisioning | null>(null);

  const begin = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.POST("/api/auth/setup/begin", {
        body: { setup_code: setupCode.trim() },
      });
      if (!data) throw toApiError(response.status, error);
      return data;
    },
    onSuccess: (data) => {
      setProvisioning(data);
      setStep("password");
    },
  });

  const complete = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.POST("/api/auth/setup/complete", {
        body: { setup_code: setupCode.trim(), password, totp_code: totpCode },
      });
      if (!response.ok) throw toApiError(response.status, error);
    },
    onSuccess: () => hardNavigate("/"),
    onError: (error) => {
      setTotpCode("");
      // 设置码中途过期或被重新生成：只能从头开始。
      if (error instanceof ApiResponseError && error.code === "setup_code_invalid") {
        begin.reset();
        setStep("code");
      }
    },
  });

  function submitCode(event: FormEvent) {
    event.preventDefault();
    complete.reset();
    begin.mutate();
  }

  function submitPassword(event: FormEvent) {
    event.preventDefault();
    const problem = passwordProblem(password, confirmPassword);
    setPasswordError(problem);
    if (!problem) setStep("scan");
  }

  function submitConfirm(event: FormEvent) {
    event.preventDefault();
    complete.mutate();
  }

  const error = begin.error ?? complete.error;

  return (
    <AuthCard title="首次设置" description={STEP_TITLES[step]}>
      {error ? <FormError error={toUiError(error)} /> : null}

      {step === "code" ? (
        <form onSubmit={submitCode} className="flex flex-col gap-4">
          <FormField
            id="setup-code"
            label="设置码"
            hint="在服务器上运行 python -m harvester.api.setup_code 得到，1 小时内有效"
          >
            <Input
              id="setup-code"
              autoComplete="off"
              required
              value={setupCode}
              onChange={(e) => setSetupCode(e.target.value)}
            />
          </FormField>
          <Button type="submit" disabled={begin.isPending}>
            {begin.isPending ? <Spinner data-icon="inline-start" /> : null}
            下一步
          </Button>
        </form>
      ) : null}

      {step === "password" ? (
        <form onSubmit={submitPassword} className="flex flex-col gap-4">
          {passwordError ? (
            <Alert variant="destructive">
              <AlertDescription>{passwordError}</AlertDescription>
            </Alert>
          ) : null}
          <FormField id="new-password" label="密码" hint="12 到 128 个字符">
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </FormField>
          <FormField id="confirm-password" label="再输入一次">
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              required
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
            />
          </FormField>
          <Button type="submit">下一步</Button>
        </form>
      ) : null}

      {step === "scan" && provisioning ? (
        <div className="flex flex-col gap-4">
          <p>用验证器 App（比如 1Password、Google Authenticator）扫这个二维码。</p>
          {/* 扫码需要深色码配浅色底，所以这里不跟主题走。 */}
          <div className="self-center rounded-md bg-white p-3">
            <QRCodeSVG value={provisioning.otpauth_uri} size={192} title="添加到验证器的二维码" />
          </div>
          <div className="flex flex-col gap-1">
            <p className="text-muted-foreground">扫不了的话，手动输入这个密钥：</p>
            <code className="rounded-sm bg-muted px-2 py-1 font-mono text-xs break-all">
              {provisioning.secret}
            </code>
          </div>
          <Button onClick={() => setStep("confirm")}>下一步</Button>
        </div>
      ) : null}

      {step === "confirm" ? (
        <form onSubmit={submitConfirm} className="flex flex-col gap-4">
          <FormField id="confirm-totp" label="验证码" hint="验证器 App 里的 6 位数字">
            <Input
              id="confirm-totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
            />
          </FormField>
          <Button type="submit" disabled={complete.isPending}>
            {complete.isPending ? <Spinner data-icon="inline-start" /> : null}
            完成设置
          </Button>
          <Button variant="ghost" type="button" onClick={() => setStep("scan")}>
            回到二维码
          </Button>
        </form>
      ) : null}
    </AuthCard>
  );
}
