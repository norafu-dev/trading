// Discord CDN 地址，以及头像和服务器图标共用的首字回退。
const CDN = "https://cdn.discordapp.com";

// 写成完整的类名字面量，Tailwind 才会生成它们；由 `fallbackColorFor` 选取。
const AVATAR_BACKGROUNDS = [
  "bg-avatar-1",
  "bg-avatar-2",
  "bg-avatar-3",
  "bg-avatar-4",
  "bg-avatar-5",
  "bg-avatar-6",
] as const;

export type AvatarBackground = (typeof AVATAR_BACKGROUNDS)[number];

function extensionFor(hash: string): "gif" | "webp" {
  return hash.startsWith("a_") ? "gif" : "webp";
}

export function avatarUrl(userId: string, avatarHash: string | null): string | null {
  if (!avatarHash) return null;
  return `${CDN}/avatars/${userId}/${avatarHash}.${extensionFor(avatarHash)}?size=80`;
}

export function guildIconUrl(guildId: string, iconHash: string | null): string | null {
  if (!iconHash) return null;
  return `${CDN}/icons/${guildId}/${iconHash}.${extensionFor(iconHash)}?size=96`;
}

export function emojiUrl(emojiId: string, animated: boolean): string {
  return `${CDN}/emojis/${emojiId}.${animated ? "gif" : "webp"}?size=48`;
}

// 取第一个字形，开头的 emoji 或组合字符也算一个字。
export function initialFor(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  const first = segmenter.segment(trimmed)[Symbol.iterator]().next();
  return first.done ? "?" : first.value.segment.toUpperCase();
}

export function fallbackColorFor(id: string): AvatarBackground {
  const index = /^\d+$/.test(id) ? Number(BigInt(id) % 6n) : 0;
  return AVATAR_BACKGROUNDS[index] ?? AVATAR_BACKGROUNDS[0];
}
