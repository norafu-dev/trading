// 登录后要回去的地址只接受站内路径，防止 `next` 被用来跳到别的网站。
// 浏览器解析网址时会去掉制表符和换行，"/\t/evil.com" 实际会变成 "//evil.com"。
function hasControlCharOrBackslash(path: string): boolean {
  return [...path].some((char) => {
    const code = char.charCodeAt(0);
    return char === "\\" || code < 0x20 || code === 0x7f;
  });
}

export function safeNextPath(next: string | null | undefined): string {
  if (!next?.startsWith("/") || next.startsWith("//") || hasControlCharOrBackslash(next)) {
    return "/";
  }
  return next;
}

export function loginHref(next: string, reason?: "expired"): string {
  const query = `next=${encodeURIComponent(next)}`;
  return reason ? `/login?${query}&reason=${reason}` : `/login?${query}`;
}
