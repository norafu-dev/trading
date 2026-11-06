// 频道页的网址格式在这里定死（spec 0003）；页面本身由第 8 项建。
// `messageId` 有值时，频道页以这条消息为中心加载（spec 0013）。
export function channelHref(channelId: string, messageId?: string): string {
  const base = `/channels?id=${encodeURIComponent(channelId)}`;
  return messageId ? `${base}&m=${encodeURIComponent(messageId)}` : base;
}

// 搜索页的网址；条件的拼法在 components/search/search-params.ts。
export function searchHref(query = ""): string {
  return query ? `/search?${query}` : "/search";
}
