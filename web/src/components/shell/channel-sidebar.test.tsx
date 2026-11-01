import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ChannelGroup } from "./build-channel-tree";
import { ChannelSidebar } from "./channel-sidebar";
import { channelHref } from "./routes";

const groups: readonly ChannelGroup[] = [
  {
    categoryId: "c1",
    name: "讨论",
    channels: [
      { id: "godot", name: "Godot 4.4 迁移", type: 0, selectable: true, threads: [] },
      {
        id: "art",
        name: "美术",
        type: 0,
        selectable: false,
        threads: [{ id: "pixel", name: "像素风格求助", type: 11, selectable: true, threads: [] }],
      },
    ],
  },
];

function renderSidebar(overrides: Partial<Parameters<typeof ChannelSidebar>[0]> = {}) {
  return render(
    <ChannelSidebar
      guildName="独立游戏开发"
      groups={groups}
      selectedChannelId="godot"
      hrefFor={channelHref}
      {...overrides}
    />,
  );
}

describe("ChannelSidebar", () => {
  // covers: AC-2
  it("选中的频道带 aria-current=page，其他频道不带", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: "Godot 4.4 迁移" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "像素风格求助" })).not.toHaveAttribute("aria-current");
  });

  // covers: AC-2
  it("频道链接指向频道页地址", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: "Godot 4.4 迁移" })).toHaveAttribute(
      "href",
      "/channels?id=godot",
    );
  });

  // covers: AC-2, AC-14
  it("没开采集的父频道显示成不可点的占位，线程仍可点", () => {
    renderSidebar();
    expect(screen.queryByRole("link", { name: "美术" })).not.toBeInTheDocument();
    expect(screen.getByText("美术").parentElement).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("link", { name: "像素风格求助" })).toBeInTheDocument();
  });

  // covers: AC-3
  it("点频道时调用 onNavigate（窄屏用它关掉抽屉）", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderSidebar({ onNavigate });
    const link = screen.getByRole("link", { name: "像素风格求助" });
    link.addEventListener("click", (event) => event.preventDefault());
    await user.click(link);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  // covers: AC-2, AC-14
  it("分类标题是可以折叠的按钮", async () => {
    const user = userEvent.setup();
    renderSidebar();
    const trigger = screen.getByRole("button", { name: "讨论" });
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  // covers: AC-13
  it("没有开启采集的频道时显示空状态", () => {
    renderSidebar({ groups: [] });
    expect(screen.getByText("还没有开启采集的频道")).toBeInTheDocument();
  });

  it("显示服务器名，没有时显示 Harvester", () => {
    const { rerender } = renderSidebar();
    expect(screen.getByRole("heading", { name: "独立游戏开发" })).toBeInTheDocument();
    rerender(
      <ChannelSidebar
        guildName={null}
        groups={groups}
        selectedChannelId={null}
        hrefFor={channelHref}
      />,
    );
    expect(screen.getByRole("heading", { name: "Harvester" })).toBeInTheDocument();
  });
});
