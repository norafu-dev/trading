import { AuthGate } from "@/components/auth/AuthGate";
import { CollectedShell } from "@/components/channels/collected-shell";
import { AccountRailLink } from "@/components/discord/account-rail-link";
import { HistoryRailLink } from "@/components/history/history-rail-link";
import { MediaRailLink } from "@/components/media/media-rail-link";
import { HomeRailLink } from "@/components/system/home-rail-link";
import { EventStreamProvider } from "@/lib/events/event-stream";

// 所有 dashboard 页面都先过登录检查，再包进外壳；侧栏显示已开启采集的频道（spec 0006）。
export default function DashboardLayout({ children }: LayoutProps<"/">) {
  return (
    <AuthGate>
      <EventStreamProvider>
        <CollectedShell
          accountLink={
            <>
              <AccountRailLink />
              <HistoryRailLink />
              <MediaRailLink />
            </>
          }
          homeLink={<HomeRailLink />}
        >
          {children}
        </CollectedShell>
      </EventStreamProvider>
    </AuthGate>
  );
}
