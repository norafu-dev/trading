import type { ReactNode } from "react";

export function PreviewSection({
  id,
  title,
  description,
  children,
}: Readonly<{ id: string; title: string; description?: string; children: ReactNode }>) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-xl font-semibold">
          {title}
        </h2>
        {description ? <p className="text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}
