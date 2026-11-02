import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithQuery } from "@/components/auth/test-utils";

import { HomeRailLink } from "./home-rail-link";
import { SYSTEM_STATUS, systemStatusResponse } from "./test-fixtures";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

describe("HomeRailLink", () => {
  beforeEach(() => {
    get.mockReset();
  });

  // covers: spec 0009 AC-7
  it("备份逾期时首页入口带红点和说明", async () => {
    get.mockResolvedValue(
      systemStatusResponse({
        ...SYSTEM_STATUS,
        backup: { last_run: null, last_success_at: null, overdue: true },
      }),
    );
    renderWithQuery(<HomeRailLink />);

    expect(
      await screen.findByRole("link", { name: "Harvester 首页，备份超过 2 天没有成功" }),
    ).toHaveAttribute("href", "/");
  });

  // covers: spec 0009 AC-7
  it("没有逾期时是普通的首页入口", async () => {
    get.mockResolvedValue(systemStatusResponse());
    renderWithQuery(<HomeRailLink />);

    await vi.waitFor(() => expect(get).toHaveBeenCalledWith("/api/system/status"));
    expect(screen.getByRole("link", { name: "Harvester 首页" })).toBeInTheDocument();
  });
});
