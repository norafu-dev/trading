"use client";

import { PlugIcon } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useId, useMemo, useState } from "react";

import { FormField } from "@/components/auth/auth-card";
import { FormError } from "@/components/auth/form-error";
import { ErrorBlock } from "@/components/states/error-block";
import { useSystemStatus } from "@/components/system/use-system-status";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { toUiError } from "@/lib/api/errors";
import { createTimeFormat } from "@/lib/format/time";
import { useNow } from "@/lib/format/use-now";

import { type AccountOperation, describeAccount, STALE_TEXT } from "./account-status";
import {
  type AccountAction,
  type DiscordAccount,
  useAccountAction,
  useAddAccount,
  useDiscordAccount,
  useReplaceToken,
  useUpdateLabel,
} from "./use-discord-account";

const ACTION_LABELS: Readonly<Record<AccountAction, string>> = {
  disable: "停用",
  enable: "启用",
  reconnect: "重新连接",
  "switch-identity": "确认换成这个账号",
};

// token 只写不读：输入框不回填，也不让浏览器记住，提交后清空。
function TokenInput({
  id,
  value,
  onChange,
}: Readonly<{ id: string; value: string; onChange: (value: string) => void }>) {
  return (
    <Input
      id={id}
      type="password"
      autoComplete="off"
      required
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function AddAccountForm() {
  const [label, setLabel] = useState("");
  const [token, setToken] = useState("");
  const add = useAddAccount();

  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate({ label, token }, { onSettled: () => setToken("") });
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 text-card-foreground">
      <Alert>
        <AlertTitle>请使用小号</AlertTitle>
        <AlertDescription>selfbot 违反 Discord 服务条款，账号可能被封。</AlertDescription>
      </Alert>
      {add.isError ? <FormError error={toUiError(add.error)} /> : null}
      <form onSubmit={submit} className="flex flex-col gap-4">
        <FormField id="discord-label" label="备注名">
          <Input
            id="discord-label"
            required
            maxLength={100}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </FormField>
        <FormField id="discord-token" label="Token">
          <TokenInput id="discord-token" value={token} onChange={setToken} />
        </FormField>
        <Button type="submit" disabled={add.isPending} className="self-start">
          {add.isPending ? <Spinner data-icon="inline-start" /> : null}
          添加账号
        </Button>
      </form>
    </section>
  );
}

function ReplaceTokenForm({ onDone }: Readonly<{ onDone: () => void }>) {
  const id = useId();
  const [token, setToken] = useState("");
  const replace = useReplaceToken();

  function submit(event: FormEvent) {
    event.preventDefault();
    replace.mutate(token, {
      onSuccess: onDone,
      onSettled: () => setToken(""),
    });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 border-t border-border pt-4">
      {replace.isError ? <FormError error={toUiError(replace.error)} /> : null}
      <FormField id={id} label="新 Token">
        <TokenInput id={id} value={token} onChange={setToken} />
      </FormField>
      <div className="flex gap-2">
        <Button type="submit" disabled={replace.isPending}>
          {replace.isPending ? <Spinner data-icon="inline-start" /> : null}
          保存
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          取消
        </Button>
      </div>
    </form>
  );
}

function LabelForm({ label, onDone }: Readonly<{ label: string; onDone: () => void }>) {
  const id = useId();
  const [value, setValue] = useState(label);
  const update = useUpdateLabel();

  function submit(event: FormEvent) {
    event.preventDefault();
    update.mutate(value, { onSuccess: onDone });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 border-t border-border pt-4">
      {update.isError ? <FormError error={toUiError(update.error)} /> : null}
      <FormField id={id} label="备注名">
        <Input
          id={id}
          required
          maxLength={100}
          value={value}
          onChange={(event) => setValue(event.target.value)}
        />
      </FormField>
      <div className="flex gap-2">
        <Button type="submit" disabled={update.isPending}>
          保存
        </Button>
        <Button type="button" variant="ghost" onClick={onDone}>
          取消
        </Button>
      </div>
    </form>
  );
}

type Editing = "none" | "token" | "label" | "confirm-switch";

function AccountCard({ account }: Readonly<{ account: DiscordAccount }>) {
  const [editing, setEditing] = useState<Editing>("none");
  const action = useAccountAction();
  const format = useMemo(
    () => createTimeFormat(Intl.DateTimeFormat().resolvedOptions().timeZone),
    [],
  );
  const info = describeAccount(account, format.fullTime);
  const close = () => setEditing("none");
  const system = useSystemStatus();
  const now = useNow();
  const processBeat = system.data?.collector.last_heartbeat_at;

  function run(operation: AccountOperation) {
    if (operation === "replace-token") {
      setEditing("token");
    } else if (operation === "switch-identity") {
      setEditing("confirm-switch");
    } else {
      action.mutate(operation);
    }
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-border bg-card p-5 text-card-foreground">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="truncate text-xl font-semibold">{account.label}</h2>
          <p className="truncate text-muted-foreground">
            {account.discord_username ?? "还没有登录过"}
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setEditing("label")}>
          改备注名
        </Button>
      </div>
      <p role="status" className="flex flex-wrap items-center gap-2">
        <Badge
          variant={
            info.tone === "ok" ? "default" : info.tone === "error" ? "destructive" : "secondary"
          }
        >
          {info.text}
        </Badge>
        {account.collector_stale ? <Badge variant="destructive">{STALE_TEXT}</Badge> : null}
      </p>
      {system.data ? (
        <p className="text-xs text-muted-foreground">
          采集进程最近心跳：
          {processBeat ? (
            <time dateTime={processBeat} title={format.fullTime(processBeat)}>
              {format.relativeTime(processBeat, now)}
            </time>
          ) : (
            "还没有心跳"
          )}
        </p>
      ) : null}
      {account.last_heartbeat_at ? (
        <p className="text-xs text-muted-foreground">
          最近心跳 {format.fullTime(account.last_heartbeat_at)}
        </p>
      ) : null}
      {action.isError ? <FormError error={toUiError(action.error)} /> : null}
      <div className="flex flex-wrap gap-2">
        {info.operations.map((operation) => (
          <Button
            key={operation}
            variant={operation === "disable" ? "outline" : "default"}
            disabled={action.isPending}
            onClick={() => run(operation)}
          >
            {operation === "replace-token" ? "更换 token" : ACTION_LABELS[operation]}
          </Button>
        ))}
      </div>
      {editing === "confirm-switch" && account.status === "account_mismatch" ? (
        <Alert variant="destructive">
          <AlertTitle>确认换成这个账号？</AlertTitle>
          <AlertDescription className="flex flex-col gap-3">
            <p>原账号的身份和频道权限记录会被清空。已采集的消息和频道开关都会保留。</p>
            <div className="flex gap-2">
              <Button
                variant="destructive"
                disabled={action.isPending}
                onClick={() => action.mutate("switch-identity", { onSuccess: close })}
              >
                确认更换
              </Button>
              <Button variant="ghost" onClick={close}>
                取消
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}
      {editing === "token" ? <ReplaceTokenForm onDone={close} /> : null}
      {editing === "label" ? <LabelForm label={account.label} onDone={close} /> : null}
    </section>
  );
}

export function AccountPanel() {
  const account = useDiscordAccount();

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-8">
        <header className="flex flex-col gap-1">
          <h1 className="flex items-center gap-2 text-3xl font-bold">
            <PlugIcon aria-hidden strokeWidth={1.75} className="size-7" />
            Discord 账号
          </h1>
          <p className="text-muted-foreground">用来采集消息的小号，状态会自动更新。</p>
        </header>
        {account.isPending ? (
          <div className="flex justify-center py-12">
            <Spinner className="size-6" />
          </div>
        ) : account.isError ? (
          <ErrorBlock error={toUiError(account.error)} onRetry={() => void account.refetch()} />
        ) : account.data === null ? (
          <AddAccountForm />
        ) : (
          <>
            <AccountCard account={account.data} />
            <Link href="/settings/channels" className="self-start text-link underline">
              管理采集频道
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
