import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./http";
import { catalogClient } from "./catalogClient";
import { demoCatalogClient } from "./demoCatalogClient";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
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
});
