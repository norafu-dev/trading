"use client";

import { useState } from "react";

import { MediaLightbox } from "./media-lightbox";
import { mediaUrl, PREVIEW_MAX_HEIGHT, PREVIEW_MAX_WIDTH, previewBox } from "./media-url";
import type { MediaView } from "./types";

type MediaImageProps = Readonly<{
  media: MediaView;
  // embed 的小图用固定边长的方框，不按比例。
  fill?: boolean;
}>;

// 已保存的图片：固定尺寸的框，加载前后行高不变；点开是原图大图。
export function MediaImage({ media, fill = false }: MediaImageProps) {
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  const box = previewBox(media);
  const src = mediaUrl(media.id, { thumb: media.hasThumb });

  const image = failed ? (
    <span className="grid size-full place-items-center p-2 text-center text-xs text-muted-foreground">
      图片加载失败
    </span>
  ) : (
    // biome-ignore lint/performance/noImgElement: 静态导出不做图片优化，图片来自自己的 api
    <img
      src={src}
      alt={media.filename}
      loading="lazy"
      onError={() => setFailed(true)}
      className={box.hasSize || fill ? "size-full object-cover" : "size-full object-contain"}
    />
  );

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={`查看大图：${media.filename}`}
        style={fill ? undefined : { aspectRatio: box.aspectRatio, maxWidth: PREVIEW_MAX_WIDTH }}
        className={
          fill
            ? "block size-full cursor-zoom-in overflow-hidden rounded-md bg-muted"
            : "mt-1 block w-full cursor-zoom-in overflow-hidden rounded-md bg-muted"
        }
      >
        <span
          className="block size-full"
          style={fill ? undefined : { maxHeight: PREVIEW_MAX_HEIGHT }}
        >
          {image}
        </span>
      </button>
      <MediaLightbox id={media.id} filename={media.filename} open={open} onOpenChange={setOpen} />
    </>
  );
}
