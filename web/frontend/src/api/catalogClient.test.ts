import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./http";
import { catalogClient } from "./catalogClient";
import { demoCatalogClient } from "./demoCatalogClient";
import { extractPolicyVersionPin, samePolicyVersionPin, validatePolicyFacetsResponse, validatePolicyRegionsResponse } from "./policyFacets";
import type { PolicyFacetsResponse, PolicyRegionsResponse } from "../types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const version = `sha256:${"a".repeat(64)}`;
const mappingVersion = `sha256:${"b".repeat(64)}`;

function facetsFixture(): PolicyFacetsResponse {
  const levels = ["national", "province", "prefecture", "county", "unclassified"] as const;
  return {
    dataset: {
      generation_id: "fixture-gen", revision: 1, total_documents: 2, partial: false,
      source: "current_published_frozen_release", region_version: version, region_mapping_version: mappingVersion,
    },
    query: {
      q: null, area: null, fyp: null, plantype: null, admin_level: null, province_code: null,
      prefecture_code: null, county_code: null, region_status: null, year: null, fyp_status: null, plantype_status: null,
    },
    facets: {
      admin_level: {
        options: levels.map(value => ({ value, label: value, count: 0 })),
        regions: {
          province_options: [], prefecture_options: [], county_options: [],
          unclassified: { label: "地区未分类", count: 0 }, not_applicable: { label: "不适用地区树", count: 0 },
        },
      },
      year: { options: [{ value: "unknown", label: "年份未知", count: 0 }] },
      fyp: { options: [{ value: "unclassified", label: "unclassified", count: 1 }], unclassified: { label: "未分类", count: 0 } },
      plantype: { options: [], unclassified: { label: "未分类", count: 0 } },
    },
  };
}

function regionsFixture(): PolicyRegionsResponse {
  return {
    region_version: version, artifact_sha256: "d".repeat(64), source: "fixture", source_url: "https://example.test",
    retrieved_at_utc: "2026-10-31T00:00:00Z", snapshot_as_of: null, official_version: null,
    node_count: 2,
    data: [{ code: "110000000000", name: "省级", source_type: "省", tier: "province", children: [
      { code: "110101000000", name: "县级", source_type: "县", tier: "county", children: [] },
    ] }],
  };
}

describe("catalogClient", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("lists policies with the documented literal search and filter query", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ items: [] }));
    await catalogClient.list({ q: "数字经济", area: "北京市", page: 2, page_size: 10 });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/api/policies?");
    const query = new URL(String(url)).searchParams;
    expect(query.get("q")).toBe("数字经济");
    expect(query.get("area")).toBe("北京市");
    expect(query.get("page")).toBe("2");
    expect(query.get("page_size")).toBe("10");
    expect(init?.method).toBe("GET");
  });

  it("encodes policy IDs and passes pagination arguments to detail and content", async () => {
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({}));
    await catalogClient.detail("policy/one");
    await catalogClient.content("policy/one", 8, 25);

    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/api/policies/policy%2Fone");
    const contentUrl = new URL(String(vi.mocked(fetch).mock.calls[1][0]));
    expect(contentUrl.pathname).toContain("/api/policies/policy%2Fone/content");
    expect(contentUrl.searchParams.get("offset")).toBe("8");
    expect(contentUrl.searchParams.get("limit")).toBe("25");
  });

  it("does not fall back to synthetic results when the real directory request fails", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(catalogClient.list()).rejects.toMatchObject({ kind: "network" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each([404, 503])("keeps existing list and read endpoints unavailable on HTTP %i without synthetic data", async status => {
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({ detail: "接口暂不可用" }, status));

    await expect(catalogClient.list()).rejects.toMatchObject({ kind: "http", status });
    await expect(catalogClient.detail("missing-policy")).rejects.toMatchObject({ kind: "http", status });
    await expect(catalogClient.content("missing-policy", 0, 4000)).rejects.toMatchObject({ kind: "http", status });

    expect(fetch).toHaveBeenCalledTimes(3);
    expect(fetch).toHaveBeenNthCalledWith(1, expect.stringContaining("/api/policies"), expect.objectContaining({ method: "GET" }));
    expect(fetch).toHaveBeenNthCalledWith(2, expect.stringContaining("/api/policies/missing-policy"), expect.objectContaining({ method: "GET" }));
    expect(fetch).toHaveBeenNthCalledWith(3, expect.stringContaining("/api/policies/missing-policy/content"), expect.objectContaining({ method: "GET" }));
  });

  it("marks demo catalog data and reports additional pages correctly", async () => {
    const first = await demoCatalogClient.content("DEMO-001", 0, 8);
    const found = await demoCatalogClient.list({ q: "数字经济" });

    expect(first.dataset.source).toBe("demo_synthetic");
    expect(first.has_more).toBe(true);
    expect(found.items[0]?.matches).toContainEqual(expect.objectContaining({ field: "body", offset: expect.any(Number) }));
  });

  it("maps HTTP errors without disguising them as empty directory results", async () => {
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({ detail: "目录不可用" }, 503));
    await expect(catalogClient.list()).rejects.toBeInstanceOf(ApiError);
    await expect(catalogClient.list()).rejects.toMatchObject({ status: 503, message: "目录不可用" });
  });

  it("requests complete facet filters and pins without pagination", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(facetsFixture()));
    const filters = {
      q: "数字经济", area: "北京市", fyp: "第十四个五年规划", plantype: "专项规划",
      admin_level: "county" as const, province_code: "110000000000", prefecture_code: "110100000000",
      county_code: "110101000000", region_status: "mapped" as const, year: 2021,
      fyp_status: "unclassified" as const, plantype_status: "unclassified" as const,
      generation_id: "fixture-gen", revision: 1, region_version: version, region_mapping_version: mappingVersion,
    };
    await catalogClient.facets(filters);
    const query = new URL(String(vi.mocked(fetch).mock.calls[0][0])).searchParams;
    for (const [key, value] of Object.entries(filters)) expect(query.get(key)).toBe(String(value));
    expect(query.has("page")).toBe(false);
    expect(query.has("page_size")).toBe(false);
  });

  it("requests a pinned region tree and validates its unique nodes and count", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(regionsFixture()));
    const result = await catalogClient.regions(version);
    const query = new URL(String(vi.mocked(fetch).mock.calls[0][0])).searchParams;
    expect(query.get("region_version")).toBe(version);
    expect(validatePolicyRegionsResponse(result).node_count).toBe(2);
    expect(() => validatePolicyRegionsResponse({ ...regionsFixture(), node_count: 3 })).toThrow(/node_count/);
    expect(() => validatePolicyRegionsResponse({
      ...regionsFixture(), data: [...regionsFixture().data, ...regionsFixture().data], node_count: 4,
    })).toThrow(/duplicate region code/);
  });

  it("extracts mapping-unavailable pins and compares the full version group and dataset metadata", () => {
    const dataset = { ...facetsFixture().dataset, region_mapping_version: null };
    expect(extractPolicyVersionPin(dataset)?.region_mapping_version).toBe("unavailable");
    expect(extractPolicyVersionPin({ ...dataset, region_version: undefined })).toBeNull();
    expect(samePolicyVersionPin(dataset, { ...dataset })).toBe(true);
    expect(samePolicyVersionPin(dataset, { ...dataset, total_documents: 3 })).toBe(false);
    expect(samePolicyVersionPin(dataset, { ...dataset, partial: true })).toBe(false);
    expect(samePolicyVersionPin(dataset, { ...dataset, revision: 2 })).toBe(false);
  });

  it("keeps missing-value sentinels separate from real values and requires all facet siblings", () => {
    const response = facetsFixture();
    expect(validatePolicyFacetsResponse(response).facets.fyp.options[0]?.value).toBe("unclassified");
    expect(response.facets.fyp.unclassified.count).toBe(0);
    expect(() => validatePolicyFacetsResponse({
      ...response, facets: { ...response.facets, year: { options: [] } },
    })).toThrow(/unknown-year/);
  });

  it.each([409, 503])("preserves HTTP %i for facets without demo fallback", async status => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "服务错误" }, status));
    await expect(catalogClient.facets()).rejects.toBeInstanceOf(ApiError);
    await expect(catalogClient.facets()).rejects.toMatchObject({ kind: "http", status });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("rejects partial version pins before issuing a request", async () => {
    expect(() => catalogClient.facets({ generation_id: "fixture-gen" })).toThrow(/必须完整提供/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects dimension endpoints explicitly in demo mode", async () => {
    vi.doMock("./config", () => ({ isRealMode: false }));
    vi.resetModules();
    try {
      const { catalogClient: demoModeClient } = await import("./catalogClient");
      await expect(demoModeClient.facets()).rejects.toThrow("演示目录暂未提供维度筛选");
      await expect(demoModeClient.regions()).rejects.toThrow("演示目录暂未提供维度筛选");
      expect(fetch).not.toHaveBeenCalled();
    } finally {
      vi.doUnmock("./config");
      vi.resetModules();
    }
  });
});
