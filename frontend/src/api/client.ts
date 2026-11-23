const TOKEN_KEY = "news-assistant-token";
export const session = {
  get: () => window.sessionStorage.getItem(TOKEN_KEY),
  set: (token: string) => window.sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => window.sessionStorage.removeItem(TOKEN_KEY),
};
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
  get temporary(): boolean {
    return (
      this.status === 0 ||
      [408, 429].includes(this.status) ||
      this.status >= 500
    );
  }
}

export function temporaryError(reason: unknown): boolean {
  return reason instanceof ApiError
    ? reason.temporary
    : reason instanceof Error &&
        ["AbortError", "TimeoutError", "TypeError"].includes(reason.name);
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  const token = session.get();
  if (token) headers.set("Authorization", "Bearer " + token);
  const response = await fetch("/api/v1" + path, { ...options, headers });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      session.clear();
      window.dispatchEvent(new Event("session-expired"));
    }
    let message = "请求失败，请稍后重试";
    if (data && typeof data === "object" && "error" in data) {
      const error = data.error;
      if (
        error &&
        typeof error === "object" &&
        "message" in error &&
        typeof error.message === "string"
      ) {
        message = error.message;
      }
    }
    throw new ApiError(message, response.status);
  }
  if (data === null) throw new ApiError("服务器暂未返回有效数据", 502);
  return data as T;
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}
