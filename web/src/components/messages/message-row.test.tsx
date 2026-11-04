import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { createTimeFormat } from "@/lib/format/time";

import type { MessageRowItem } from "./build-rows";
import { MessageRow } from "./message-row";
import type { EmbedView, MentionResolver, MessageView } from "./types";

const format = createTimeFormat("Asia/Shanghai");
const resolver: MentionResolver = { user: () => undefined, channel: () => undefined };
const NOW = new Date("2026-10-30T10:00:00Z");

function message(overrides: Partial<MessageView> = {}): MessageView {
  return {
    id: "1",
    authorId: "100",
    authorName: "坏头像同学",
    authorAvatarUrl: null,
    createdAt: "2026-10-30T06:05:32Z",
    content: "你好",
    embeds: [],
    forwarded: null,
    attachments: [],
    editedAt: null,
    replyTo: null,
    deleted: false,
    ...overrides,
  };
}

function embed(overrides: Partial<EmbedView> = {}): EmbedView {
  return {
    type: "rich",
    title: "Godot 4.4 发布",
    description: null,
    url: null,
    color: null,
    timestamp: null,
    author: null,
    fields: [],
    footerText: null,
    imageUrl: null,
    thumbnailUrl: null,
    imageMedia: null,
    thumbnailMedia: null,
    ...overrides,
  };
}

// 解析器传 failed，正文按纯文本显示，不需要加载 WASM
function renderRow(overrides: Partial<MessageView> = {}, groupStart = true) {
  const msg = message(overrides);
  const row: MessageRowItem = { kind: "message", key: msg.id, message: msg, groupStart };
  return render(
    <MessageRow row={row} format={format} now={NOW} resolver={resolver} parser="failed" />,
  );
}

describe("MessageRow", () => {
  // covers: spec 0008 AC-20
  it("回复显示引用行，摘要是纯文字；原消息没采集和已删除各有说明", () => {
    const { unmount } = renderRow({
      replyTo: { missing: false, authorName: "小鹿", preview: "**不解析**", deleted: true },
    });
    expect(screen.getByText("小鹿")).toBeInTheDocument();
    expect(screen.getByText("**不解析**")).toBeInTheDocument();
    expect(screen.getByText("（已删除）")).toBeInTheDocument();
    unmount();

    renderRow({ replyTo: { missing: true } });
    expect(screen.getByText("原消息未采集")).toBeInTheDocument();
  });

  // covers: AC-5, AC-6
  it("组首显示名字和时间，可访问名称带作者和完整时间", () => {
    renderRow();
    const article = screen.getByRole("article", {
      name: "坏头像同学，2026年10月30日 星期五 14:05:32",
    });
    expect(within(article).getByText("14:05")).toHaveAttribute(
      "title",
      "2026年10月30日 星期五 14:05:32",
    );
  });

  // covers: AC-5
  it("同组后面的消息不显示名字，只在左侧放 HH:mm", () => {
    renderRow({}, false);
    expect(screen.queryByText("坏头像同学")).not.toBeInTheDocument();
    expect(screen.getByText("14:05").tagName).toBe("TIME");
  });

  // covers: AC-12
  it("没有头像时显示名字的第一个字", () => {
    renderRow();
    expect(screen.getByText("坏")).toBeInTheDocument();
  });

  // covers: AC-11
  it("编辑过的消息显示「（已编辑）」，悬停显示编辑时间", () => {
    renderRow({ editedAt: "2026-10-30T07:00:00Z" });
    expect(screen.getByText("（已编辑）")).toHaveAttribute(
      "title",
      "编辑于 2026年10月30日 星期五 15:00:00",
    );
  });

  it("没编辑过的消息不显示「（已编辑）」", () => {
    renderRow();
    expect(screen.queryByText("（已编辑）")).not.toBeInTheDocument();
  });

  // covers: AC-11
  it("已删除的消息照常显示内容，并带「已删除」标签", () => {
    renderRow({ deleted: true });
    expect(screen.getByText(/你好/)).toBeInTheDocument();
    expect(screen.getByText("已删除")).toBeInTheDocument();
  });

  it("没删除的消息没有「已删除」标签", () => {
    renderRow();
    expect(screen.queryByText("已删除")).not.toBeInTheDocument();
  });

  // covers: AC-11
  it("附件显示文件名和按 1024 进位的大小", () => {
    renderRow({
      attachments: [
        { id: "a", filename: "a.txt", contentType: null, sizeBytes: 1023, media: null },
        { id: "b", filename: "b.png", contentType: null, sizeBytes: 1024, media: null },
        { id: "c", filename: "c.zip", contentType: null, sizeBytes: 1_572_864, media: null },
      ],
    });
    for (const [name, size] of [
      ["a.txt", "1023 B"],
      ["b.png", "1.0 KB"],
      ["c.zip", "1.5 MB"],
    ]) {
      expect(screen.getByText(name as string).parentElement).toHaveTextContent(size as string);
    }
  });

  // covers: AC-11
  it("embed 有颜色时左边色带用这个颜色", () => {
    renderRow({ embeds: [embed({ color: 0x478cbf })] });
    const card = screen.getByText("Godot 4.4 发布").closest("div.grid") as HTMLElement;
    expect(card.style.borderLeftColor).toBe("rgb(71, 140, 191)");
  });

  // covers: AC-11
  it("embed 没有颜色时不写行内颜色，色带用边框色", () => {
    renderRow({ embeds: [embed({ color: null })] });
    const card = screen.getByText("Godot 4.4 发布").closest("div.grid") as HTMLElement;
    expect(card.style.borderLeftColor).toBe("");
  });

  // covers: AC-9, AC-11
  it("embed 标题的链接经过 safeUrl，javascript: 地址不生成链接", () => {
    renderRow({ embeds: [embed({ url: "javascript:alert(1)" })] });
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("embed 标题有 https 地址时是新标签打开的链接", () => {
    renderRow({ embeds: [embed({ url: "https://godotengine.org" })] });
    const link = screen.getByRole("link", { name: "Godot 4.4 发布" });
    expect(link).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(link).toHaveAttribute("target", "_blank");
  });

  // covers: AC-11
  it("转发的消息显示「转发」标签、文字和附件名", () => {
    renderRow({
      forwarded: {
        messageId: "9",
        channelId: "8",
        guildId: null,
        content: "被转发的话",
        embeds: [],
        attachments: [{ filename: "设计稿.fig", media: null }],
      },
    });
    expect(screen.getByText("转发")).toBeInTheDocument();
    expect(screen.getByText("被转发的话")).toBeInTheDocument();
    expect(screen.getByText("设计稿.fig")).toBeInTheDocument();
  });
});
