import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import { ExportBar } from "./export-bar";
import { type ExportSelection, toEnd } from "./export-selection";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { GET: get } }));

const A = toEnd({ id: "10", createdAt: "2026-11-06T07:30:00Z", authorName: "nora" });
const B = toEnd({ id: "20", createdAt: "2026-11-06T08:30:00Z", authorName: "kol" });
const READY: ExportSelection = { step: "ready", start: A, end: B };

function preview(overrides: Record<string, unknown> = {}) {
  return {
    count: 3,
    over_limit: false,
    range_start: { id: "10", created_at: "2026-11-06T07:30:00Z" },
    range_end: { id: "20", created_at: "2026-11-06T08:30:00Z" },
    gaps: [],
    ...overrides,
  };
}

function served(data: unknown) {
  get.mockResolvedValue({ data, ...jsonResponse(200) });
}

function renderBar(selection: ExportSelection = READY, handlers = {}) {
  return renderWithQuery(
    <ExportBar
      channelId="1"
      selection={selection}
      timeZone="Asia/Shanghai"
      onToggleOpen={vi.fn()}
      onCancel={vi.fn()}
      {...handlers}
    />,
  );
}

describe("ExportBar", () => {
  beforeEach(() => get.mockReset());

  it("只选了一端时提示再点一条，下载不可用，也不请求预览（AC-3）", () => {
    renderBar({ step: "one", first: A });
    expect(screen.getByText(/再点一条消息/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeDisabled();
    expect(get).not.toHaveBeenCalled();
  });

  it("只选了快捷按钮时提示另一个快捷按钮", () => {
    renderBar({ step: "one-open", side: "start" });
    expect(screen.getByText("再点一条消息，或选另一个快捷按钮")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "从最早" })).toHaveAttribute("aria-pressed", "true");
  });

  it("范围确定后显示两端的时间和作者，并显示条数（AC-4、AC-6）", async () => {
    served(preview());
    renderBar();
    expect(screen.getByText("正在统计")).toBeInTheDocument();
    expect(await screen.findByText("共 3 条")).toBeInTheDocument();
    expect(screen.getByText(/nora/)).toBeInTheDocument();
    expect(screen.getByText(/kol/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeEnabled();
    expect(get.mock.calls[0]?.[1].params.query).toEqual({ start: "10", end: "20" });
  });

  it("开放的一端显示最早或最新，且不传参数", async () => {
    served(preview());
    renderBar({ step: "ready", start: { kind: "open" }, end: B });
    await screen.findByText("共 3 条");
    expect(screen.getByText("最早")).toBeInTheDocument();
    expect(get.mock.calls[0]?.[1].params.query).toEqual({ start: undefined, end: "20" });
  });

  it("超过上限：不显示精确条数，下载不可用（AC-7）", async () => {
    served(preview({ count: 100001, over_limit: true }));
    renderBar();
    expect(await screen.findByText("超过 10 万条上限，请缩小范围")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeDisabled();
  });

  it("空频道：显示频道里没有消息，下载不可用（AC-9）", async () => {
    served(preview({ count: 0, range_start: null, range_end: null }));
    renderBar({ step: "ready", start: { kind: "open" }, end: { kind: "open" } });
    expect(await screen.findByText("频道里没有消息")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeDisabled();
  });

  it("缺口提示带补采链接，不拦下载（AC-8）", async () => {
    served(
      preview({
        gaps: [
          { start: "2026-11-06T07:30:00Z", end: "2026-11-06T08:00:00Z" },
          { start: "2026-11-06T08:10:00Z", end: "2026-11-06T08:20:00Z" },
        ],
      }),
    );
    renderBar();
    expect(await screen.findByText(/这段时间里有 2 段没有采集/)).toHaveTextContent("共约 40 分钟");
    expect(screen.getByRole("link", { name: "去补采" })).toHaveAttribute("href", "/history");
    expect(screen.getByRole("button", { name: "下载" })).toBeEnabled();
  });

  it("预览超时：显示统计超时和重试（AC-9）", async () => {
    get.mockResolvedValueOnce({
      data: undefined,
      error: { error: { code: "export_timeout", message: "统计超时" } },
      ...jsonResponse(503),
    });
    served(preview());
    renderBar();
    expect(await screen.findByText(/统计超时，请缩小范围/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下载" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("共 3 条")).toBeInTheDocument();
  });

  it("下载：用临时 <a download> 打开带参数的网址，格式可切换，留在选择模式（AC-10）", async () => {
    served(preview());
    const clicked: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this.getAttribute("href") ?? "");
    });
    const onCancel = vi.fn();
    renderBar(READY, { onCancel });
    await screen.findByText("共 3 条");

    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    await userEvent.click(screen.getByRole("button", { name: "JSONL" }));
    await userEvent.click(screen.getByRole("button", { name: "下载" }));

    expect(clicked).toEqual([
      "/api/channels/1/export?start=10&end=20&format=csv&tz=Asia%2FShanghai",
      "/api/channels/1/export?start=10&end=20&format=jsonl&tz=Asia%2FShanghai",
    ]);
    expect(onCancel).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("快捷按钮和取消把事件交给上层", async () => {
    served(preview());
    const onToggleOpen = vi.fn();
    const onCancel = vi.fn();
    renderBar(READY, { onToggleOpen, onCancel });
    await userEvent.click(screen.getByRole("button", { name: "到最新" }));
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onToggleOpen).toHaveBeenCalledWith("end");
    expect(onCancel).toHaveBeenCalled();
    await waitFor(() => expect(get).toHaveBeenCalled());
  });
});
