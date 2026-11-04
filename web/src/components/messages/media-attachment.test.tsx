import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithQuery } from "@/components/auth/test-utils";

import { MediaAttachment } from "./media-attachment";
import { kindOfMedia, mediaUrl } from "./media-url";
import type { MediaView } from "./types";

const { post } = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("@/lib/api/client", () => ({ api: { POST: post } }));

function media(overrides: Partial<MediaView> = {}): MediaView {
  return {
    id: "7",
    status: "stored",
    filename: "shot.png",
    contentType: "image/png",
    sizeBytes: 2048,
    width: 800,
    height: 600,
    hasThumb: true,
    ...overrides,
  };
}

describe("MediaAttachment", () => {
  beforeEach(() => {
    post.mockReset();
  });

  // covers: spec 0011 AC-15
  it("已保存的图片显示缩略图，不碰 Discord 原链接", () => {
    render(<MediaAttachment filename="shot.png" media={media()} />);
    const img = screen.getByAltText("shot.png");
    expect(img).toHaveAttribute("src", "/api/media/7?variant=thumb");
  });

  it("没有缩略图时用原图", () => {
    render(<MediaAttachment filename="shot.png" media={media({ hasThumb: false })} />);
    expect(screen.getByAltText("shot.png")).toHaveAttribute("src", "/api/media/7");
  });

  it("已保存的视频和音频直接显示播放器", () => {
    const { container, rerender } = render(
      <MediaAttachment
        filename="v.mp4"
        media={media({ filename: "v.mp4", contentType: "video/mp4" })}
      />,
    );
    expect(container.querySelector("video")).toHaveAttribute("preload", "metadata");
    rerender(
      <MediaAttachment
        filename="a.mp3"
        media={media({ filename: "a.mp3", contentType: "audio/mpeg" })}
      />,
    );
    expect(container.querySelector("audio")).toHaveAttribute("src", "/api/media/7");
  });

  it("其他已保存文件是下载链接，SVG 也不内联预览", () => {
    render(
      <MediaAttachment
        filename="logo.svg"
        media={media({ filename: "logo.svg", contentType: "image/svg+xml" })}
      />,
    );
    expect(screen.getByRole("link")).toHaveAttribute("href", "/api/media/7?download=1");
  });

  it.each([
    ["pending", "保存中"],
    ["too_large", "超过上限，未保存"],
    ["no_space", "空间不足，未保存"],
    ["failed", "保存失败"],
    ["gone", "Discord 上已失效"],
  ] as const)("%s 显示「%s」", (status, text) => {
    renderWithQuery(<MediaAttachment filename="x.bin" media={media({ status })} />);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByText("x.bin")).toBeInTheDocument();
  });

  // covers: spec 0011 AC-17
  it("只有失败和已失效的附件有「重试」按钮", () => {
    for (const status of ["pending", "too_large", "no_space"] as const) {
      const { unmount } = renderWithQuery(
        <MediaAttachment filename="x.bin" media={media({ status })} />,
      );
      expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
      unmount();
    }
    renderWithQuery(<MediaAttachment filename="x.bin" media={media({ status: "gone" })} />);
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("点「重试」调接口，并乐观地改成「保存中」", async () => {
    post.mockResolvedValue({ data: { id: "7", status: "pending" }, ...jsonResponse(200) });
    renderWithQuery(<MediaAttachment filename="x.bin" media={media({ status: "failed" })} />);

    await userEvent.click(screen.getByRole("button", { name: "重试" }));

    await waitFor(() => expect(screen.getByText("保存中")).toBeInTheDocument());
    expect(post).toHaveBeenCalledWith("/api/media/{media_id}/retry", {
      params: { path: { media_id: 7 } },
    });
  });

  it("没有 media 行时按原来的文件条显示", () => {
    render(<MediaAttachment filename="old.fig" sizeBytes={1024} media={null} />);
    expect(screen.getByText("old.fig")).toBeInTheDocument();
    expect(screen.getByText("1.0 KB")).toBeInTheDocument();
  });
});

describe("media-url", () => {
  it("拼接缩略图和下载参数", () => {
    expect(mediaUrl("5", { thumb: true, download: true })).toBe(
      "/api/media/5?variant=thumb&download=1",
    );
  });

  it("按类型分类", () => {
    expect(kindOfMedia({ contentType: "image/jpeg; charset=x" })).toBe("image");
    expect(kindOfMedia({ contentType: null })).toBe("file");
  });
});
