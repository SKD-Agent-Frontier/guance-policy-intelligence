import { REAL_REQUEST_TIMEOUT_MS } from "./config";
import { API_REQUEST_TIMEOUT_MS, requestJson } from "./http";
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
  submit(request: ResearchRequest): Promise<RunResult> {
    return requestJson<RunResult>(
      "/api/runs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
      { timeoutMs: REAL_REQUEST_TIMEOUT_MS },
    );
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
