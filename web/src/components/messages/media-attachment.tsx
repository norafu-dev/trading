import { AttachmentChip } from "./attachment-chip";
import { MediaImage } from "./media-image";
import { MediaPlayer } from "./media-player";
import { MediaStatusText } from "./media-status";
import { kindOfMedia, mediaUrl } from "./media-url";
import { RetryMediaButton } from "./retry-media-button";
import type { MediaView } from "./types";

type MediaAttachmentProps = Readonly<{
  filename: string;
  sizeBytes?: number | null;
  // 没有 media 行（上线前的转发等）时按原来的文件条显示。
  media: MediaView | null;
}>;

// 一个附件按保存状态显示（spec 0011 AC-15）。前端不再从 Discord 原链接加载任何附件。
export function MediaAttachment({ filename, sizeBytes, media }: MediaAttachmentProps) {
  if (!media) return <AttachmentChip filename={filename} sizeBytes={sizeBytes} />;
  if (media.status !== "stored") {
    return (
      <AttachmentChip filename={filename} sizeBytes={sizeBytes ?? media.sizeBytes}>
        <MediaStatusText status={media.status} />
        {media.status === "failed" || media.status === "gone" ? (
          <RetryMediaButton mediaId={media.id} />
        ) : null}
      </AttachmentChip>
    );
  }
  switch (kindOfMedia(media)) {
    case "image":
      return <MediaImage media={media} />;
    case "video":
      return <MediaPlayer media={media} kind="video" />;
    case "audio":
      return <MediaPlayer media={media} kind="audio" />;
    case "file":
      return (
        <AttachmentChip
          filename={filename}
          sizeBytes={media.sizeBytes ?? sizeBytes}
          href={mediaUrl(media.id, { download: true })}
        />
      );
  }
}
