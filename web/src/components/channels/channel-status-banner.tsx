import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";

import type { ChannelDetail } from "./use-channel-detail";

type Reason = NonNullable<ChannelDetail["banner_reason"]>;

export const BANNER_TEXT: Readonly<Record<Reason, string>> = {
  channel_deleted: "这个频道已被删除，下面是之前采集到的消息",
  guild_left: "小号已退出这个服务器，下面是之前采集到的消息",
  collect_off: "这个频道已关闭采集，新消息不会保存",
  no_access: "小号已失去这个频道的读取权限，采集已暂停",
  thread_archived: "这个线程已归档，重新激活后会继续采集",
  parent_unavailable: "这个线程的父频道已不可用，采集已暂停",
  account_reconnecting: "账号正在重连，恢复后会继续采集",
  account_offline: "账号不在线，上线后恢复采集",
  starting: "正在开始采集，几秒后会收到新消息",
};

const BANNER_LINKS: Readonly<Partial<Record<Reason, Readonly<{ href: string; label: string }>>>> = {
  collect_off: { href: "/settings/channels", label: "管理采集频道" },
  account_offline: { href: "/settings/discord", label: "查看账号" },
};

// 频道没有在采集时，消息列表上方的一条提示。原因由后端算好（AC-23）。
export function ChannelStatusBanner({ reason }: Readonly<{ reason: Reason }>) {
  const link = BANNER_LINKS[reason];
  return (
    <div className="shrink-0 border-b border-border px-4 py-2">
      <Alert>
        <AlertDescription>
          {BANNER_TEXT[reason]}
          {link ? (
            <>
              {" "}
              <Link href={link.href} className="text-link underline">
                {link.label}
              </Link>
            </>
          ) : null}
        </AlertDescription>
      </Alert>
    </div>
  );
}
