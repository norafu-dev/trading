const ALLOWED_PROTOCOLS: ReadonlySet<string> = new Set(["http:", "https:"]);

const URL_LIKE = /^(https?:\/\/)?([\p{L}\p{N}-]+\.)+[\p{L}\p{N}-]{2,}(:\d+)?([/?#]\S*)?$/iu;

// dashboard 渲染的每个链接（正文、embed 标题、作者、图片）都经过这里：
// 只放行 http 和 https，其他协议（javascript:、data: 等）都当普通文字显示。
export function safeUrl(raw: string | null | undefined): URL | null {
  if (!raw) return null;
  try {
    const url = new URL(raw);
    return ALLOWED_PROTOCOLS.has(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

export function looksLikeUrl(text: string): boolean {
  return URL_LIKE.test(text.trim());
}

function hostOf(text: string): string | null {
  const trimmed = text.trim();
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
  try {
    return new URL(withScheme).hostname.toLowerCase();
  } catch {
    return null;
  }
}

// 带文字链接的防钓鱼检查：链接文字像网址，但实际指向别的域名。
export function hostDiffers(text: string, url: URL): boolean {
  const shown = hostOf(text);
  return shown !== null && shown !== url.hostname.toLowerCase();
}
