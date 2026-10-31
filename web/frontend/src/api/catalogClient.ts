import { isRealMode } from "./config";
import { demoCatalogClient } from "./demoCatalogClient";
import { API_REQUEST_TIMEOUT_MS, requestJson } from "./http";
import type {
  PolicyContentResponse,
  PolicyDetailResponse,
  PolicyFacetRequest,
  PolicyFacetsResponse,
  PolicyListParams,
  PolicyListResponse,
  PolicyRegionsResponse,
} from "../types";
import { validatePolicyFacetsResponse, validatePolicyRegionsResponse } from "./policyFacets";

const DEMO_FACETS_UNAVAILABLE = "演示目录暂未提供维度筛选";

function assertCompletePin(params: PolicyListParams | PolicyFacetRequest): void {
  const values = params as unknown as Record<string, unknown>;
  const pinKeys = ["generation_id", "revision", "region_version", "region_mapping_version"];
  if (!pinKeys.some(key => values[key] !== undefined)) return;
  if (pinKeys.some(key => values[key] === undefined)) {
    throw new TypeError("版本固定参数必须完整提供 generation_id、revision、region_version、region_mapping_version");
  }
  if (typeof values.generation_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(values.generation_id)
    || !Number.isInteger(values.revision) || (values.revision as number) < 1
    || typeof values.region_version !== "string" || !/^sha256:[0-9a-f]{64}$/.test(values.region_version)
    || (values.region_mapping_version !== "unavailable"
      && (typeof values.region_mapping_version !== "string" || !/^sha256:[0-9a-f]{64}$/.test(values.region_mapping_version)))) {
    throw new TypeError("版本固定参数格式无效");
  }
}

export const catalogClient = {
  list(params: PolicyListParams = {}, signal?: AbortSignal): Promise<PolicyListResponse> {
    if (!isRealMode) return demoCatalogClient.list(params, signal);
    assertCompletePin(params);
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

  facets(params: PolicyFacetRequest = {}, signal?: AbortSignal): Promise<PolicyFacetsResponse> {
    if (!isRealMode) return Promise.reject(new Error(DEMO_FACETS_UNAVAILABLE));
    assertCompletePin(params);
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) query.set(key, String(value));
    }
    return requestJson<unknown>(
      `/api/policies/facets${query.size ? `?${query.toString()}` : ""}`,
      { method: "GET" },
      { signal, timeoutMs: API_REQUEST_TIMEOUT_MS },
    ).then(validatePolicyFacetsResponse);
  },

  regions(regionVersion?: string, signal?: AbortSignal): Promise<PolicyRegionsResponse> {
    if (!isRealMode) return Promise.reject(new Error(DEMO_FACETS_UNAVAILABLE));
    const query = new URLSearchParams();
    if (regionVersion !== undefined) query.set("region_version", regionVersion);
    return requestJson<unknown>(
      `/api/policies/regions${query.size ? `?${query.toString()}` : ""}`,
      { method: "GET" },
      { signal, timeoutMs: API_REQUEST_TIMEOUT_MS },
    ).then(validatePolicyRegionsResponse);
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
