import { mediaUrl, PREVIEW_MAX_WIDTH, previewBox } from "./media-url";
import type { MediaView } from "./types";

// 视频和音频直接显示播放器；只读元数据，点播放才下载（spec 0011 AC-15）。
export function MediaPlayer({
  media,
  kind,
}: Readonly<{ media: MediaView; kind: "video" | "audio" }>) {
  const src = mediaUrl(media.id);
  if (kind === "audio") {
    return (
      // biome-ignore lint/a11y/useMediaCaption: 用户自己的附件，没有字幕轨道
      <audio
        controls
        preload="metadata"
        src={src}
        aria-label={media.filename}
        className="mt-1 w-full max-w-[400px]"
      />
    );
  }
  return (
    // biome-ignore lint/a11y/useMediaCaption: 用户自己的附件，没有字幕轨道
    <video
      controls
      preload="metadata"
      src={src}
      aria-label={media.filename}
      style={{ aspectRatio: previewBox(media).aspectRatio, maxWidth: PREVIEW_MAX_WIDTH }}
      className="mt-1 block w-full rounded-md bg-muted"
    />
  );
}
