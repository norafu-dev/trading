import { ButtonGallery } from "./component-gallery";
import { PreviewSection } from "./preview-section";
import { EmptyShellDemo, ShellDemo } from "./shell-demo";
import { TokenSwatches } from "./token-swatches";

// 只在开发时存在的预览页（spec 0003）：`*.dev.tsx` 在 `next dev` 下是页面，`next build` 会忽略它。
export default function PreviewPage() {
  return (
    <div className="h-dvh overflow-y-auto">
      <main className="mx-auto flex max-w-5xl flex-col gap-12 px-4 py-10 md:px-8">
        <header className="flex flex-col gap-2">
          <h1 className="text-3xl font-bold tracking-tight">组件预览</h1>
          <p className="text-muted-foreground">
            dashboard 的 token、基础组件和全部状态。只在 <code>pnpm dev</code> 下存在。
          </p>
        </header>
        <PreviewSection
          id="tokens"
          title="颜色 token"
          description="跟随系统的浅色和深色主题；切换系统外观可以看到另一套。"
        >
          <TokenSwatches />
        </PreviewSection>
        <PreviewSection id="buttons" title="按钮和徽章">
          <ButtonGallery />
        </PreviewSection>
        <PreviewSection
          id="shell"
          title="外壳"
          description="图标栏、频道列表和主区域；窗口窄于 768px 时收进抽屉。"
        >
          <ShellDemo />
        </PreviewSection>
        <PreviewSection id="shell-empty" title="外壳：还没有开启采集的频道">
          <EmptyShellDemo />
        </PreviewSection>
      </main>
    </div>
  );
}
