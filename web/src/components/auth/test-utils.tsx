import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

export function renderWithQuery(ui: ReactElement) {
  // 重试次数由 hook 自己定，这里只把等待压到 0，测试不用真的等。
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

export function jsonResponse(status: number) {
  return { response: new Response(null, { status }) };
}
