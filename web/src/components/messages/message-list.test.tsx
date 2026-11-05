import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { UiError } from "@/lib/api/errors";
import { useMarkdownParser } from "@/lib/discord-markdown/use-markdown-parser";

import { MessageList } from "./message-list";
import type { MessageListProps, MessageView } from "./types";

// 解析器的加载是外部边界（WASM），这里直接指定它的状态。
vi.mock("@/lib/discord-markdown/use-markdown-parser", () => ({ useMarkdownParser: vi.fn() }));

const parserStatus = vi.mocked(useMarkdownParser);

const serverError: UiError = {
  kind: "server",
  title: "服务器出错了",
  detail: "稍后再试。",
  retryable: true,
};

const sample: MessageView = {
  id: "1",
  authorId: "100",
  authorName: "阿青",
  authorAvatarUrl: null,
  createdAt: "2026-10-30T06:05:00Z",
  content: "第一条消息",
  embeds: [],
  forwarded: null,
  attachments: [],
  editedAt: null,
  replyTo: null,
  deleted: false,
};

function props(overrides: Partial<MessageListProps> = {}): MessageListProps {
  return {
    messages: [],
    resolver: { user: () => undefined, channel: () => undefined },
    now: new Date("2026-10-30T10:00:00Z"),
    timeZone: "Asia/Shanghai",
    hasOlder: false,
    isLoadingOlder: false,
    onReachTop: vi.fn(),
    status: "ready",
    ...overrides,
  };
}

const skeletonRows = (container: HTMLElement) =>
  container.querySelectorAll('[role="status"] > div').length;

describe("MessageList", () => {
  beforeEach(() => {
    parserStatus.mockReturnValue("failed");
  });

  // covers: AC-13
  it("首次加载中显示 8 行消息骨架", () => {
    const { container } = render(<MessageList {...props({ status: "loading" })} />);
    expect(screen.getByRole("status", { name: "正在加载消息" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(skeletonRows(container)).toBe(8);
  });

  // covers: AC-13
  it.each([
    ["status 为 empty", props({ status: "empty" })],
    ["status 为 ready 但没有消息", props({ status: "ready", messages: [] })],
  ])("%s 时显示空状态", (_label, listProps) => {
    render(<MessageList {...listProps} />);
    expect(screen.getByText("这个频道还没有采集到消息")).toBeInTheDocument();
  });

  // covers: AC-13
  it("首次加载失败时显示错误块，点「重试」调用 onRetry", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<MessageList {...props({ status: "error", error: serverError, onRetry })} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("服务器出错了");
    expect(alert).toHaveTextContent("稍后再试。");
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("错误不可重试时（比如需要重新登录）不显示「重试」", () => {
    const unauthorized: UiError = {
      kind: "unauthorized",
      title: "需要重新登录",
      detail: "",
      retryable: false,
    };
    render(<MessageList {...props({ status: "error", error: unauthorized, onRetry: vi.fn() })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("需要重新登录");
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  // covers: AC-10
  it("解析器还没就绪时有消息也先显示骨架", () => {
    parserStatus.mockReturnValue("pending");
    render(<MessageList {...props({ messages: [sample] })} />);
    expect(screen.getByRole("status", { name: "正在加载消息" })).toBeInTheDocument();
    expect(screen.queryByRole("log")).not.toBeInTheDocument();
  });

  // covers: AC-10
  it("解析器加载失败时显示消息列表（正文按纯文本）", () => {
    render(<MessageList {...props({ messages: [sample] })} />);
    expect(screen.getByRole("log", { name: "消息" })).toBeInTheDocument();
  });

  // covers: AC-8
  it("加载更早的消息时顶部显示转圈提示", () => {
    render(
      <MessageList {...props({ messages: [sample], isLoadingOlder: true, hasOlder: true })} />,
    );
    expect(screen.getByText("正在加载更早的消息")).toBeInTheDocument();
  });

  // covers: AC-8
  it("加载更早的消息失败时顶部出现重试，列表本身仍在", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(
      <MessageList
        {...props({ messages: [sample], hasOlder: true, olderError: serverError, onRetry })}
      />,
    );
    expect(screen.getByText("服务器出错了，更早的消息没加载出来")).toBeInTheDocument();
    expect(screen.getByRole("log", { name: "消息" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  // covers: spec 0013 AC-19
  it("还没加载到最新时显示「回到最新」，点击走 onJumpLatest", async () => {
    const user = userEvent.setup();
    const onJumpLatest = vi.fn();
    render(<MessageList {...props({ messages: [sample], hasNewer: true, onJumpLatest })} />);
    await screen.findByRole("log", { name: "消息" });
    await user.click(await screen.findByRole("button", { name: /回到最新/ }));
    expect(onJumpLatest).toHaveBeenCalledTimes(1);
  });

  // covers: spec 0013 AC-19
  it("向下加载时底部转圈，失败时底部出现重试，列表仍在", async () => {
    const user = userEvent.setup();
    const onRetryNewer = vi.fn();
    const { rerender } = render(
      <MessageList {...props({ messages: [sample], hasNewer: true, isLoadingNewer: true })} />,
    );
    expect(screen.getByText("正在加载更新的消息")).toBeInTheDocument();

    rerender(
      <MessageList
        {...props({ messages: [sample], hasNewer: true, newerError: serverError, onRetryNewer })}
      />,
    );
    expect(screen.getByText("服务器出错了，更新的消息没加载出来")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetryNewer).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("log", { name: "消息" })).toBeInTheDocument();
  });
});
