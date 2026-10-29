import { apiBaseUrl, REAL_REQUEST_TIMEOUT_MS } from "./config";
import type { ResearchRequest, RunResult } from "../types";

export type ApiFailureKind = "network" | "timeout" | "http" | "malformed";

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
  422: "请求参数无效，请检查研究问题与政策编号。",
  503: "服务尚未就绪，后端仍在加载模型或知识库，请稍后重试。",
  500: "服务内部错误，请稍后重试。",
};

export function describeFailure(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "未知错误";
}

async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    /* 非 JSON 响应体不额外报错 */
  }
  return null;
}

export const realClient = {
  async submit(request: ResearchRequest): Promise<RunResult> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REAL_REQUEST_TIMEOUT_MS);

    let response: Response;
    try {
      response = await fetch(apiBaseUrl + "/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: request.question,
          task_type: request.task_type,
          targets: request.targets ?? null,
          dimensions: request.dimensions ?? null,
          session_id: request.session_id ?? null,
        }),
        signal: controller.signal,
      });
    } catch {
      if (controller.signal.aborted) {
        throw new ApiError("timeout", "请求超时。Agent 可能仍在后台执行，请稍后重试。");
      }
      throw new ApiError("network", "无法连接研究服务，请确认后端已启动。");
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      const detail = await readDetail(response);
      const fallback = HTTP_MESSAGES[response.status] ?? "请求失败（HTTP " + response.status + "）。";
      throw new ApiError("http", detail ?? fallback, response.status);
    }

    try {
      return (await response.json()) as RunResult;
    } catch {
      throw new ApiError("malformed", "服务返回内容无法解析。", response.status);
    }
  },
};