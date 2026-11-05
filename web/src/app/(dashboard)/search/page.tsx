import { Suspense } from "react";

import { SearchPage } from "@/components/search/search-page";
import { ListSkeleton } from "@/components/states/list-skeleton";

export default function SearchRoutePage() {
  return (
    <Suspense fallback={<ListSkeleton />}>
      <SearchPage />
    </Suspense>
  );
}
