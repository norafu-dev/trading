import { Suspense } from "react";

import { ChannelPage } from "@/components/channels/channel-page";
import { ListSkeleton } from "@/components/states/list-skeleton";

export default function ChannelMessagesPage() {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <ChannelPage />
    </Suspense>
  );
}
