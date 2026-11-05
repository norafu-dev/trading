import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/components/auth/test-utils";
import { SYSTEM_STATUS, systemStatusResponse } from "@/components/system/test-fixtures";
import { TooltipProvider } from "@/components/ui/tooltip";

import { HistoryRailLink } from "./history-rail-link";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

function renderLink() {
  return renderWithQuery(
    <TooltipProvider>
      <HistoryRailLink />
    </TooltipProvider>,
  );
}

describe("HistoryRailLink", () => {
  beforeEach(() => {
    get.mockReset();
  });

  // covers: spec 0012 AC-7
  it("因限速自动暂停时有红点，aria-label 说明原因", async () => {
    get.mockResolvedValue(
      systemStatusResponse({
        ...SYSTEM_STATUS,
        history: { ...SYSTEM_STATUS.history, paused_reason: "rate_limited" },
      }),
    );
    const { container } = renderLink();

    expect(await screen.findByRole("link", { name: "历史采集已因限速暂停" })).toHaveAttribute(
      "href",
      "/history",
    );
    expect(container.querySelector(".bg-destructive")).not.toBeNull();
  });

  // covers: spec 0012 AC-7
  it("手动暂停和账号风险暂停不亮红点", async () => {
    get.mockResolvedValue(
      systemStatusResponse({
        ...SYSTEM_STATUS,
        history: { ...SYSTEM_STATUS.history, paused_reason: "manual" },
      }),
    );
    const { container } = renderLink();

    await vi.waitFor(() => expect(get).toHaveBeenCalledWith("/api/system/status"));
    expect(screen.getByRole("link", { name: "历史采集" })).toBeInTheDocument();
    expect(container.querySelector(".bg-destructive")).toBeNull();
  });
});
