// Dev only gallery (spec 0003): `*.dev.tsx` is a page under `next dev` and ignored by `next build`.
export default function PreviewPage() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-6 p-8">
      <h1 className="text-page-title font-bold">组件预览</h1>
      <article data-message-row className="flex gap-4 px-4 py-1">
        <div className="size-10 shrink-0 rounded-full bg-avatar-1" />
        <div className="min-w-0">
          <p className="text-message font-semibold">Nora</p>
          <p className="text-message">第一条纯文本消息 __HARVESTER_FAKE_DATA__</p>
        </div>
      </article>
    </main>
  );
}
