import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, describeFailure, realClient } from "./realClient";
import type { ResearchRequest } from "../types";

const REQUEST: ResearchRequest = {
  question: "苏州市养老服务人才培养有哪些要求",
  task_type: "qa",
  targets: ["20699"],
  dimensions: ["政策工具"],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("realClient", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("posts the contract payload to /api/runs and returns the RunResult", async () => {
    const result = { run_id: "r1", status: "completed", evidence_status: "verified" };
    vi.mocked(fetch).mockResolvedValue(jsonResponse(result));

    await expect(realClient.submit(REQUEST)).resolves.toMatchObject({ run_id: "r1" });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/api/runs");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      question: REQUEST.question,
      task_type: "qa",
      targets: ["20699"],
      dimensions: ["政策工具"],
      session_id: null,
    });
  });

  it("sends session_id when provided and nulls optional fields otherwise", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ run_id: "r2" }));

    await realClient.submit({ question: "q", task_type: "organize", session_id: "s-1" });

    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
    expect(body.session_id).toBe("s-1");
    expect(body.targets).toBeNull();
    expect(body.dimensions).toBeNull();
  });

  it("maps HTTP 422 / 503 / 500 to readable Chinese errors", async () => {
    for (const [status, expected] of [
      [422, "请求参数无效"],
      [503, "服务尚未就绪"],
      [500, "服务内部错误"],
    ] as const) {
      vi.mocked(fetch).mockResolvedValue(jsonResponse({}, status));
      const error = await realClient.submit(REQUEST).catch((e) => e);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(status);
      expect((error as ApiError).message).toContain(expected);
    }
  });

  it("reports network failure without falling back to demo data", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await realClient.submit(REQUEST).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("network");
    expect(describeFailure(error)).toContain("无法连接研究服务");
  });

  it("reports malformed JSON instead of returning a fake result", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("not json", { status: 200 }));
    const error = await realClient.submit(REQUEST).catch((e) => e);
    expect((error as ApiError).kind).toBe("malformed");
  });
});