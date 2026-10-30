import { isRealMode } from "./config";
import { demoCatalogClient } from "./demoCatalogClient";
import { API_REQUEST_TIMEOUT_MS, requestJson } from "./http";
import type {
  PolicyContentResponse,
  PolicyDetailResponse,
  PolicyListParams,
  PolicyListResponse,
} from "../types";

export const catalogClient = {
  list(params: PolicyListParams = {}, signal?: AbortSignal): Promise<PolicyListResponse> {
    if (!isRealMode) return demoCatalogClient.list(params, signal);
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) query.set(key, String(value));
    }
    return requestJson<PolicyListResponse>(
      `/api/policies${query.size ? `?${query.toString()}` : ""}`,
      { method: "GET" },
      { signal, timeoutMs: API_REQUEST_TIMEOUT_MS },
    );
  },

  detail(planid: string, signal?: AbortSignal): Promise<PolicyDetailResponse> {
    if (!isRealMode) return demoCatalogClient.detail(planid, signal);
    return requestJson<PolicyDetailResponse>(
      `/api/policies/${encodeURIComponent(planid)}`,
      { method: "GET" },
      { signal, timeoutMs: API_REQUEST_TIMEOUT_MS },
    );
  },

  content(
    planid: string,
    offset = 0,
    limit = 4000,
    signal?: AbortSignal,
  ): Promise<PolicyContentResponse> {
    if (!isRealMode) return demoCatalogClient.content(planid, offset, limit, signal);
    const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    return requestJson<PolicyContentResponse>(
      `/api/policies/${encodeURIComponent(planid)}/content?${query.toString()}`,
      { method: "GET" },
      { signal, timeoutMs: API_REQUEST_TIMEOUT_MS },
    );
  },
};
