import type { ReactNode } from "react";

import { Label } from "@/components/ui/label";

// 登录和设置页共用：没有侧栏，居中一张卡片。
export function AuthCard({
  title,
  description,
  children,
}: Readonly<{ title: string; description?: string; children: ReactNode }>) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-8">
      <section className="flex w-full max-w-sm flex-col gap-5 rounded-lg border border-border bg-card p-6 text-card-foreground">
        <header className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">{title}</h1>
          {description ? <p className="text-muted-foreground">{description}</p> : null}
        </header>
        {children}
      </section>
    </main>
  );
}

export function FormField({
  id,
  label,
  hint,
  children,
}: Readonly<{ id: string; label: string; hint?: string; children: ReactNode }>) {
  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
