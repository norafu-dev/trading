// 频道页的网址格式在这里定死（spec 0003）；页面本身由第 8 项建。
export function channelHref(channelId: string): string {
  return `/channels?id=${encodeURIComponent(channelId)}`;
}
