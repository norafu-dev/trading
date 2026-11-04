// 把失败的请求转成界面能显示的状态，全前端只有这一个入口（spec 0003）。
export type UiError = Readonly<{
  kind: "network" | "unauthorized" | "not_found" | "server" | "rejected" | "unknown";
  title: string;
  detail: string;
  retryable: boolean;
}>;

// 接口返回非 2xx 状态码时，由 query 函数抛出。
export class ApiResponseError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    message?: string,
    details?: Readonly<{ code?: string; retryAfterSeconds?: number }>,
  ) {
    super(message ?? `request failed (${status})`);
    this.name = "ApiResponseError";
    this.status = status;
    this.code = details?.code ?? null;
    this.retryAfterSeconds = details?.retryAfterSeconds ?? null;
  }
}

// 后端统一错误格式：{"error": {"code", "message", "retry_after_seconds"?}}（spec 0004）。
export function toApiError(status: number, body: unknown): ApiResponseError {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return new ApiResponseError(status);
  }
  const error = body.error;
  if (typeof error !== "object" || error === null) return new ApiResponseError(status);
  const code = "code" in error && typeof error.code === "string" ? error.code : undefined;
  const retryAfterSeconds =
    "retry_after_seconds" in error && typeof error.retry_after_seconds === "number"
      ? error.retry_after_seconds
      : undefined;
  return new ApiResponseError(status, undefined, { code, retryAfterSeconds });
}

function formatWait(seconds: number): string {
  return seconds < 60 ? `${seconds} 秒` : `${Math.ceil(seconds / 60)} 分钟`;
}

const REJECTED_BY_CODE: Readonly<Record<string, Pick<UiError, "title" | "detail">>> = {
  bad_credentials: { title: "登录失败", detail: "密码或验证码不对。" },
  setup_code_invalid: {
    title: "设置码无效",
    detail: "设置码不对或已过期，请在服务器上重新运行命令。",
  },
  totp_invalid: { title: "验证码不对", detail: "看一下验证器 App 里的最新验证码，再试一次。" },
  already_set_up: { title: "已经设置过了", detail: "请直接登录。" },
  setup_required: { title: "还没有完成设置", detail: "请先完成首次设置。" },
  account_exists: {
    title: "已经有一个账号了",
    detail: "这一版只支持一个账号，刷新页面看看它的状态。",
  },
  account_not_found: { title: "账号不存在", detail: "还没有添加账号，刷新页面后重新添加。" },
  invalid_account_state: {
    title: "现在不能这样操作",
    detail: "账号状态刚刚变了，请看最新状态再操作。",
  },
  token_format_invalid: {
    title: "token 的格式不对",
    detail: "请粘贴账号 token 本身，不要带空格，也不要带 Bot 前缀。",
  },
  channel_not_collectable: { title: "不能采集", detail: "这种类型的频道不能采集。" },
  channel_no_access: { title: "没有权限", detail: "小号读不了这个频道。" },
  channel_unavailable: { title: "频道不可用", detail: "频道已删除，或者已经退出这个服务器。" },
  channel_not_found: { title: "频道不存在", detail: "刷新页面看看最新的频道列表。" },
  guild_not_found: { title: "服务器不存在", detail: "刷新页面看看最新的服务器列表。" },
  guild_unavailable: { title: "服务器不可用", detail: "小号已退出这个服务器，或者服务器已删除。" },
  category_not_found: { title: "分类不存在", detail: "刷新页面看看最新的频道列表。" },
  not_a_category: { title: "不是分类", detail: "刷新页面看看最新的频道列表。" },
  bad_origin: { title: "请求被拒绝", detail: "请求来源不被允许，请从 dashboard 的网址打开。" },
  invalid_range: { title: "时间段不正确", detail: "开始时间要早于结束时间。" },
  too_many_channels: { title: "频道太多了", detail: "每次最多选 50 个频道，分几次建任务。" },
};

function fromCode(error: ApiResponseError): UiError | null {
  if (error.code === "rate_limited") {
    const wait = error.retryAfterSeconds;
    return {
      kind: "rejected",
      title: "尝试次数太多",
      detail: wait ? `请 ${formatWait(wait)}后再试。` : "请稍后再试。",
      retryable: false,
    };
  }
  const known = error.code ? REJECTED_BY_CODE[error.code] : undefined;
  return known ? { kind: "rejected", retryable: false, ...known } : null;
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
  if (error instanceof ApiResponseError) return fromCode(error) ?? fromStatus(error.status);
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
