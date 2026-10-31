// 把失败的请求转成界面能显示的状态，全前端只有这一个入口（spec 0003）。
export type UiError = Readonly<{
  kind: "network" | "unauthorized" | "not_found" | "server" | "unknown";
  title: string;
  detail: string;
  retryable: boolean;
}>;

// 接口返回非 2xx 状态码时，由 query 函数抛出。
export class ApiResponseError extends Error {
  readonly status: number;

  constructor(status: number, message?: string) {
    super(message ?? `request failed (${status})`);
    this.name = "ApiResponseError";
    this.status = status;
  }
}

function fromStatus(status: number): UiError {
  if (status === 401) {
    return {
      kind: "unauthorized",
      title: "需要重新登录",
      detail: "登录已过期，请重新登录。",
      retryable: false,
    };
  }
  if (status === 404) {
    return {
      kind: "not_found",
      title: "找不到内容",
      detail: "要找的内容不存在或已被移除。",
      retryable: false,
    };
  }
  if (status >= 500) {
    return {
      kind: "server",
      title: "服务器出错了",
      detail: `服务器返回了错误（${status}），稍后再试。`,
      retryable: true,
    };
  }
  return {
    kind: "unknown",
    title: "请求失败",
    detail: `服务器拒绝了这个请求（${status}）。`,
    retryable: true,
  };
}

export function toUiError(error: unknown): UiError {
  if (error instanceof ApiResponseError) return fromStatus(error.status);
  // 完全连不上服务器时，fetch 会以 TypeError 失败。
  if (error instanceof TypeError) {
    return {
      kind: "network",
      title: "连不上服务器",
      detail: "检查网络，或者确认服务正在运行。",
      retryable: true,
    };
  }
  return {
    kind: "unknown",
    title: "出了点问题",
    detail: "发生了意外错误，可以重试一次。",
    retryable: true,
  };
}
