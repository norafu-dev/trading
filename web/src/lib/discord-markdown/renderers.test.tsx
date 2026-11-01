import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { MentionResolver } from "@/components/messages/types";
import { createTimeFormat } from "@/lib/format/time";

import type { InlineNode, MarkdownNode } from "./parser";
import { renderNodes } from "./renderers";

const resolver: MentionResolver = {
  user: (id) => (id === "1" ? "阿青" : undefined),
  channel: (id) => (id === "2" ? "闲聊" : undefined),
};
const format = createTimeFormat("Asia/Shanghai");
const NOW = new Date("2026-10-30T06:05:32Z");

// 剧透的显示状态由 MessageContent 持有，这里用一个最小的外层组件代替它。
function Harness({ nodes }: Readonly<{ nodes: readonly MarkdownNode[] }>) {
  const [revealed, setRevealed] = useState<ReadonlySet<number>>(() => new Set());
  const reveal = (id: number) => setRevealed((current) => new Set([...current, id]));
  return <div>{renderNodes(nodes, { resolver, now: NOW, format, revealed, reveal })}</div>;
}

const text = (value: string): InlineNode => ({ type: "text", value });
const link = (url: string, label?: string): MarkdownNode => ({
  type: "link",
  value: label === undefined ? { url } : { url, text: [text(label)] },
});
const mention = (value: unknown): MarkdownNode => ({ type: "mention", value }) as MarkdownNode;

describe("链接", () => {
  // covers: AC-9
  it("javascript: 链接显示成普通文字，不生成 <a>", () => {
    render(<Harness nodes={[link("javascript:alert(1)", "点我")]} />);
    expect(screen.getByText("点我").closest("a")).toBeNull();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("data: 链接也显示成普通文字", () => {
    render(<Harness nodes={[link("data:text/html,hi")]} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("data:text/html,hi")).toBeInTheDocument();
  });

  it("https 链接在新标签打开，带安全的 rel，悬停显示真实地址", () => {
    render(<Harness nodes={[link("https://example.com/docs", "文档")]} />);
    const anchor = screen.getByRole("link", { name: "文档" });
    expect(anchor).toHaveAttribute("href", "https://example.com/docs");
    expect(anchor).toHaveAttribute("target", "_blank");
    expect(anchor).toHaveAttribute("rel", "noopener noreferrer nofollow");
    expect(anchor).toHaveAttribute("title", "https://example.com/docs");
  });

  it("链接文字像网址但域名不同时，在后面显示真实域名", () => {
    render(<Harness nodes={[link("https://evil.com/login", "https://a.com")]} />);
    expect(screen.getByText("（实际指向 evil.com）")).toBeInTheDocument();
  });

  it("链接文字不像网址时不显示真实域名提示", () => {
    render(<Harness nodes={[link("https://evil.com", "点这里")]} />);
    expect(screen.queryByText(/实际指向/)).not.toBeInTheDocument();
  });
});

describe("剧透", () => {
  // covers: AC-9, AC-14
  const spoiler: MarkdownNode = {
    type: "spoiler",
    value: { id: 7, content: [text("凶手是管家")] },
  };

  it("默认遮住，是一个 aria-expanded 为 false 的按钮", () => {
    render(<Harness nodes={[spoiler]} />);
    const button = screen.getByRole("button", { name: "剧透，按回车显示" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("凶手是管家")).toHaveAttribute("aria-hidden", "true");
  });

  it("点击后显示内容", async () => {
    const user = userEvent.setup();
    render(<Harness nodes={[spoiler]} />);
    await user.click(screen.getByRole("button"));
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("凶手是管家")).toHaveAttribute("aria-hidden", "false");
  });

  it.each(["{Enter}", " "])("用 Tab 聚焦后按 %s 也能显示", async (key) => {
    const user = userEvent.setup();
    render(<Harness nodes={[spoiler]} />);
    await user.tab();
    expect(screen.getByRole("button")).toHaveFocus();
    await user.keyboard(key);
    expect(screen.getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  it("拆到多个块里的同一个剧透一起显示", async () => {
    const user = userEvent.setup();
    const second: MarkdownNode = { type: "spoiler", value: { id: 7, content: [text("第二段")] } };
    render(<Harness nodes={[spoiler, second]} />);
    const [first] = screen.getAllByRole("button");
    await user.click(first as HTMLElement);
    for (const button of screen.getAllByRole("button")) {
      expect(button).toHaveAttribute("aria-expanded", "true");
    }
  });
});

describe("提及", () => {
  // covers: AC-9
  it.each([
    ["查得到的用户显示名字", { type: "user", value: 1n }, "@阿青"],
    ["查不到的用户显示占位", { type: "user", value: 999n }, "@未知用户"],
    ["查得到的频道显示名字", { type: "channel", value: 2n }, "#闲聊"],
    ["查不到的频道显示占位", { type: "channel", value: 999n }, "#未知频道"],
    ["身份组统一显示", { type: "role", value: 5n }, "@身份组"],
    ["@everyone", { type: "everyone" }, "@everyone"],
    ["@here", { type: "here" }, "@here"],
  ])("%s", (_label, value, expected) => {
    render(<Harness nodes={[mention(value)]} />);
    expect(screen.getByText(expected)).toBeInTheDocument();
  });
});

describe("自定义表情", () => {
  // covers: AC-9
  it("动态表情请求 gif，静态表情请求 webp", () => {
    render(
      <Harness
        nodes={[
          {
            type: "emoji",
            value: { type: "custom", value: { id: 1n, name: "wave", animated: true } },
          },
          {
            type: "emoji",
            value: { type: "custom", value: { id: 2n, name: "pepe", animated: false } },
          },
        ]}
      />,
    );
    expect(screen.getByRole("img", { name: ":wave:" })).toHaveAttribute(
      "src",
      expect.stringMatching(/\/emojis\/1\.gif/),
    );
    expect(screen.getByRole("img", { name: ":pepe:" })).toHaveAttribute(
      "src",
      expect.stringMatching(/\/emojis\/2\.webp/),
    );
  });

  it("图片加载失败时显示 :名字:", () => {
    render(
      <Harness
        nodes={[
          {
            type: "emoji",
            value: { type: "custom", value: { id: 1n, name: "wave", animated: false } },
          },
        ]}
      />,
    );
    fireEvent.error(screen.getByRole("img"));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText(":wave:")).toBeInTheDocument();
  });
});

describe("时间戳", () => {
  // covers: AC-9
  const seconds = BigInt(Date.parse("2026-10-30T06:05:32Z") / 1000);

  it("按格式字母显示，悬停显示完整时间", () => {
    render(<Harness nodes={[{ type: "timestamp", value: { value: seconds, style: "t" } }]} />);
    const time = screen.getByText("14:05");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("title", "2026年10月30日 星期五 14:05:32");
  });

  it("非法的格式字母显示原文", () => {
    render(<Harness nodes={[{ type: "timestamp", value: { value: seconds, style: "x" } }]} />);
    expect(screen.getByText(`<t:${seconds}:x>`)).toBeInTheDocument();
  });
});

describe("其他节点", () => {
  // covers: AC-9
  it("粗体、斜体、下划线、删除线、行内代码用对应的元素", () => {
    const { container } = render(
      <Harness
        nodes={[
          { type: "bold", value: [text("粗")] },
          { type: "italic", value: [text("斜")] },
          { type: "underline", value: [text("下")] },
          { type: "strikethrough", value: [text("删")] },
          { type: "code", value: "x = 1" },
        ]}
      />,
    );
    expect(container.querySelector("strong")).toHaveTextContent("粗");
    expect(container.querySelector("em")).toHaveTextContent("斜");
    expect(container.querySelector("u")).toHaveTextContent("下");
    expect(container.querySelector("s")).toHaveTextContent("删");
    expect(container.querySelector("code")).toHaveTextContent("x = 1");
  });

  it("代码块显示语言名和原样内容", () => {
    render(
      <Harness
        nodes={[{ type: "code_block", value: { language: "ts", content: "<b>不是 HTML</b>" } }]}
      />,
    );
    expect(screen.getByText("ts")).toBeInTheDocument();
    expect(screen.getByText("<b>不是 HTML</b>").closest("pre")).not.toBeNull();
  });

  it("有序列表从指定的数字开始", () => {
    render(
      <Harness
        nodes={[
          {
            type: "list",
            value: {
              type: "ordered",
              value: 3,
              items: [{ content: [{ type: "paragraph", value: [text("三")] }] }],
            },
          },
        ]}
      />,
    );
    expect(screen.getByRole("list")).toHaveAttribute("start", "3");
    expect(screen.getByRole("listitem")).toHaveTextContent("三");
  });

  it("不认识的节点按它的文字内容显示", () => {
    const unknown = { type: "future_syntax", value: [text("新语法")] } as unknown as MarkdownNode;
    render(<Harness nodes={[unknown]} />);
    expect(screen.getByText("新语法")).toBeInTheDocument();
  });
});
