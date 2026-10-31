import { AppShell } from "@/components/shell/app-shell";

// 所有 dashboard 页面都包在外壳里。服务器和频道列表由第 7 项接上，在那之前是空列表。
export default function DashboardLayout({ children }: LayoutProps<"/">) {
  return (
    <AppShell guilds={[]} channels={[]} collectorStatus="unknown">
      {children}
    </AppShell>
  );
}
