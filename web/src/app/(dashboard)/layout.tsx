import { AuthGate } from "@/components/auth/AuthGate";
import { AccountRailLink } from "@/components/discord/account-rail-link";
import { AppShell } from "@/components/shell/app-shell";
import { EventStreamProvider } from "@/lib/events/event-stream";

// 所有 dashboard 页面都先过登录检查，再包进外壳。服务器和频道列表由第 7 项接上，在那之前是空列表。
export default function DashboardLayout({ children }: LayoutProps<"/">) {
  return (
    <AuthGate>
      <EventStreamProvider>
        <AppShell
          guilds={[]}
          channels={[]}
          collectorStatus="unknown"
          accountLink={<AccountRailLink />}
        >
          {children}
        </AppShell>
      </EventStreamProvider>
    </AuthGate>
  );
}
