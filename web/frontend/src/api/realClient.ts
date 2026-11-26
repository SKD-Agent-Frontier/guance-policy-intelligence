import { REAL_REQUEST_TIMEOUT_MS } from "./config";
import { API_REQUEST_TIMEOUT_MS, ApiError, requestJson } from "./http";
import { validatePreparationResponse } from "./validation";
import type {
  PreparationResponse,
  CompareResolution,
  ResearchResolution,
  TaskType,
  ResearchPreparationRequest,
  ResearchRequest,
  RunResult,
} from "../types";

export { ApiError, describeFailure } from "./http";
export type { ApiFailureKind } from "./http";
export { validatePreparationResponse } from "./validation";

export const realClient = {
  resolveResearch(mode: TaskType, question: string): Promise<ResearchResolution> {
    return requestJson<ResearchResolution>(
      "/api/research/resolve",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode, question }) },
      { timeoutMs: API_REQUEST_TIMEOUT_MS },
    );
  },
  resolveCompare(question: string): Promise<CompareResolution> {
    return requestJson<CompareResolution>(
      "/api/research/resolve-compare",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) },
      { timeoutMs: API_REQUEST_TIMEOUT_MS },
    );
  },
  async submit(request: ResearchRequest): Promise<RunResult> {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const requestId = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
    try {
      return await requestJson<RunResult>(
        "/api/runs",
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Request-ID": requestId },
          body: JSON.stringify(request),
        },
        { timeoutMs: REAL_REQUEST_TIMEOUT_MS },
      );
    } catch (error) {
      if (error instanceof ApiError) {
        throw new ApiError(error.kind, error.message + "（请求编号 " + requestId + "）", error.status);
      }
      throw error;
    }
  },

  prepare(request: ResearchPreparationRequest): Promise<PreparationResponse> {
    return requestJson<unknown>(
      "/api/research/prepare",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
      { timeoutMs: API_REQUEST_TIMEOUT_MS },
    ).then((response) => validatePreparationResponse(response, request.mode));
  },
};
