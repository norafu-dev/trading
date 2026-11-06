import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { MentionResolver } from "@/components/messages/types";
import { createTimeFormat } from "@/lib/format/time";

import { MessageContent } from "./MessageContent";
import { type BlockNode, parseMarkdown } from "./parser";

// WASM 是外部边界：这里只替换 parseMarkdown 的返回值，渲染逻辑用真的。
vi.mock("./parser", () => ({ parseMarkdown: vi.fn() }));

const parse = vi.mocked(parseMarkdown);
const resolver: MentionResolver = { channel: () => undefined };
const baseProps = {
  resolver,
  now: new Date("2026-10-30T06:00:00Z"),
  format: createTimeFormat("Asia/Shanghai"),
};
const CONTENT = "第一行 **粗**\n第二行";

const paragraph = (value: string): BlockNode => ({
  type: "paragraph",
  value: [{ type: "text", value }],
});

describe("MessageContent", () => {
  beforeEach(() => {
    parse.mockReset();
  });

  // covers: spec 0015
  it("解析前去掉 @ 提及，纯文本降级时也看不到", () => {
    parse.mockReturnValue({ ok: true, blocks: [paragraph("早上好")] });
    render(<MessageContent content="<@1> @everyone 早上好" parser="ready" {...baseProps} />);
    expect(parse).toHaveBeenCalledWith("早上好");

    const { container } = render(
      <MessageContent content={"<@&2> 看这里\n<@3>"} parser="failed" {...baseProps} />,
    );
    expect(container.firstElementChild?.textContent).toBe("看这里");
  });

  // covers: AC-10
  it("解析器加载失败时显示纯文本，保留换行，不调用解析", () => {
    const { container } = render(
      <MessageContent content={CONTENT} parser="failed" {...baseProps} />,
    );
    const plain = container.firstElementChild;
    expect(plain).toHaveTextContent("第一行 **粗**");
    expect(plain?.textContent).toBe(CONTENT);
    expect(plain).toHaveClass("whitespace-pre-wrap");
    expect(parse).not.toHaveBeenCalled();
  });

  // covers: AC-10
  it("单条消息解析出错时，这一条显示原始文字", () => {
    parse.mockReturnValue({ ok: false, error: new Error("bad input") });
    const { container } = render(
      <MessageContent content={CONTENT} parser="ready" {...baseProps} />,
    );
    expect(container.firstElementChild?.textContent).toBe(CONTENT);
  });

  // covers: AC-10
  it("渲染某个节点出错时也降级成原始文字，不让页面白屏", () => {
    const broken = { type: "paragraph", value: null } as unknown as BlockNode;
    parse.mockReturnValue({ ok: true, blocks: [broken] });
    const { container } = render(
      <MessageContent content={CONTENT} parser="ready" {...baseProps} />,
    );
    expect(container.firstElementChild?.textContent).toBe(CONTENT);
  });

  // covers: AC-9
  it("解析成功时按语法树渲染", () => {
    parse.mockReturnValue({
      ok: true,
      blocks: [
        { type: "paragraph", value: [{ type: "bold", value: [{ type: "text", value: "粗" }] }] },
      ],
    });
    render(<MessageContent content="**粗**" parser="ready" {...baseProps} />);
    expect(screen.getByText("粗").tagName).toBe("STRONG");
    expect(screen.queryByText("**粗**")).not.toBeInTheDocument();
  });

  // covers: AC-11
  it("最后一块是段落时，后缀接在同一行末尾", () => {
    parse.mockReturnValue({ ok: true, blocks: [paragraph("你好")] });
    render(
      <MessageContent
        content="你好"
        parser="ready"
        suffix={<span>（已编辑）</span>}
        {...baseProps}
      />,
    );
    expect(screen.getByText("（已编辑）").parentElement).toHaveTextContent("你好（已编辑）");
  });

  it("内容为空时只显示后缀，没有后缀就什么都不渲染", () => {
    const { container, rerender } = render(
      <MessageContent content="" parser="ready" {...baseProps} />,
    );
    expect(container).toBeEmptyDOMElement();
    rerender(
      <MessageContent content="" parser="ready" suffix={<span>（已编辑）</span>} {...baseProps} />,
    );
    expect(screen.getByText("（已编辑）")).toBeInTheDocument();
  });
});
