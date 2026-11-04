// 附件文件的地址（spec 0011 AC-14）：都走 api，由 api 验登录后跳到存储的签名链接。
import type { MediaView } from "./types";

export function mediaUrl(id: string, options?: { thumb?: boolean; download?: boolean }): string {
  const params = new URLSearchParams();
  if (options?.thumb) params.set("variant", "thumb");
  if (options?.download) params.set("download", "1");
  const query = params.toString();
  return `/api/media/${encodeURIComponent(id)}${query ? `?${query}` : ""}`;
}

// 和后端 INLINE_TYPES 里的图片一致：SVG 不在里面，所以只显示文件条。
const PREVIEW_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "image/bmp",
  "image/avif",
]);

export type MediaKindOfView = "image" | "video" | "audio" | "file";

export function kindOfMedia(media: Pick<MediaView, "contentType">): MediaKindOfView {
  const type = media.contentType?.split(";")[0]?.trim().toLowerCase() ?? "";
  if (PREVIEW_IMAGE_TYPES.has(type)) return "image";
  if (type === "video/mp4" || type === "video/webm") return "video";
  if (type.startsWith("audio/")) return "audio";
  return "file";
}

// 预览框的宽高比：有尺寸按尺寸，没有就 16:9。宽度上限 400px 沿用 spec 0003。
export const PREVIEW_MAX_WIDTH = 400;
export const PREVIEW_MAX_HEIGHT = 300;

export function previewBox(media: Pick<MediaView, "width" | "height">): {
  aspectRatio: string;
  hasSize: boolean;
} {
  const { width, height } = media;
  if (width && height && width > 0 && height > 0) {
    return { aspectRatio: `${width} / ${height}`, hasSize: true };
  }
  return { aspectRatio: "16 / 9", hasSize: false };
}
