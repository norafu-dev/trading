import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ServerRail } from "./server-rail";
import type { GuildInput } from "./types";

const guilds: readonly GuildInput[] = [
  { id: "g1", name: "🎧 音乐分享", iconHash: null },
  { id: "g2", name: "独立游戏开发", iconHash: null },
];

function renderRail(onSelectGuild = vi.fn()) {
  render(
    <ServerRail
      guilds={guilds}
      selectedGuildId="g2"
      onSelectGuild={onSelectGuild}
      collectorStatus="unknown"
    />,
  );
  return onSelectGuild;
}

describe("ServerRail", () => {
  // covers: AC-2
  it("当前服务器的按钮带 aria-current=true，其他不带", () => {
    renderRail();
    expect(screen.getByRole("button", { name: "独立游戏开发" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByRole("button", { name: "🎧 音乐分享" })).not.toHaveAttribute("aria-current");
  });

  // covers: AC-2
  it("点服务器图标调用 onSelectGuild 并传入它的 id", async () => {
    const user = userEvent.setup();
    const onSelectGuild = renderRail();
    await user.click(screen.getByRole("button", { name: "🎧 音乐分享" }));
    expect(onSelectGuild).toHaveBeenCalledWith("g1");
  });

  // covers: AC-12
  it("没有图标时显示服务器名的第一个字，emoji 也算一个字", () => {
    renderRail();
    expect(screen.getByText("🎧")).toBeInTheDocument();
    expect(screen.getByText("独")).toBeInTheDocument();
  });

  // covers: AC-2
  it("采集进程状态未知时显示对应的提示", () => {
    renderRail();
    expect(screen.getByRole("img", { name: "采集进程状态未知" })).toBeInTheDocument();
  });

  // covers: AC-14
  it("从首页链接开始，用 Tab 能依次走到每个服务器按钮", async () => {
    const user = userEvent.setup();
    renderRail();
    await user.tab();
    expect(screen.getByRole("link", { name: "Harvester 首页" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "🎧 音乐分享" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "独立游戏开发" })).toHaveFocus();
  });
});
