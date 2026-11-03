// 整页跳转：内存里的查询缓存（包括采集数据）随之清空，登录和登出都用它。
export function hardNavigate(href: string): void {
  window.location.assign(href);
}

export function currentPath(): string {
  return `${window.location.pathname}${window.location.search}`;
}
