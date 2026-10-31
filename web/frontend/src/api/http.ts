import { apiBaseUrl } from "./config";

export type ApiFailureKind = "network" | "timeout" | "http" | "malformed" | "aborted";

export class ApiError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number | null;

  constructor(kind: ApiFailureKind, message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

const HTTP_MESSAGES: Record<number, string> = {
  400: "请求参数无效，请检查研究问题与政策编号。",
  409: "研究准备状态已失效或冲突，请重新开始准备。",
  404: "未找到该政策。",
  422: "请求参数无效，请检查研究问题与政策编号。",
  503: "服务尚未就绪，后端仍在加载模型或知识库，请稍后重试。",
  500: "服务内部错误，请稍后重试。",
};

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    return null;
  }
  return null;
}

export async function requestJson<T>(
  path: string,
  init: RequestInit,
  options: { signal?: AbortSignal; timeoutMs: number },
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort();
  if (options.signal?.aborted) controller.abort();
  else options.signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs);

  let responseReceived = false;
  try {
    const response = await fetch(apiBaseUrl + path, { ...init, signal: controller.signal });
    responseReceived = true;
    if (!response.ok) {
      const detail = await readDetail(response);
      const preparationUnavailable = response.status === 404 && path === "/api/research/prepare";
      const fallback = preparationUnavailable
        ? "研究准备服务尚未部署。"
        : HTTP_MESSAGES[response.status] ?? `请求失败（HTTP ${response.status}）。`;
      throw new ApiError("http", preparationUnavailable ? fallback : detail ?? fallback, response.status);
    }
    return (await response.json()) as T;
  } catch (error) {
    if (timedOut) throw new ApiError("timeout", "请求超时，请稍后重试。");
    if (controller.signal.aborted) {
      throw new ApiError("aborted", "请求已取消。");
    }
    if (error instanceof ApiError) throw error;
    if (responseReceived) throw new ApiError("malformed", "服务返回内容无法解析。");
    throw new ApiError("network", "无法连接研究服务，请确认后端已启动。");
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }

}

export function describeFailure(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "未知错误";
}

export const API_REQUEST_TIMEOUT_MS = 30000;
