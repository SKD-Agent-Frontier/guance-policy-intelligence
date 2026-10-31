import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, describeFailure, realClient } from "./realClient";
import type { ResearchRequest } from "../types";

const REQUEST: ResearchRequest = {
  question: "苏州市养老服务人才培养有哪些要求",
  task_type: "qa",
  targets: ["20699"],
  dimensions: ["政策工具"],
};

function preparationResponse(mode: string, status: "ready" | "needs_clarification") {
  return {
    status,
    mode,
    message: "演示准备响应",
    options: status === "ready" ? [] : [{ id: "aspect", question: "请说明关注方面", choices: [] }],
    state: {
      state_id: "opaque",
      revision: 2,
      expires_at: "2026-10-30T12:00:00Z",
      mode,
      original_request: "原始请求",
      topic: null,
      time_scope: null,
      area: null,
      dimensions: [],
      confirmed_conditions: [],
      pending_conditions: [],
      completed_questions: [],
      clarification_count: 0,
      condition_statuses: [],
    },
    request: status === "ready" ? { task_type: mode, question: "原样请求", targets: null, dimensions: [] } : null,
    warnings: [],
  };
}

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
    });
  });

  it("preserves the submitted request object without injecting optional null fields", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ run_id: "r2" }));

    await realClient.submit({ question: "q", task_type: "organize", session_id: "s-1" });

    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body));
    expect(body).toEqual({ question: "q", task_type: "organize", session_id: "s-1" });
  });

  it("preserves explicit nullable fields and leaves absent fields absent", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ run_id: "r3" }));
    const request = { question: "q", task_type: "qa" as const, targets: null, dimensions: ["政策工具"] };

    await realClient.submit(request);

    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(request);
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

  it("posts only the selected mode, message, and opaque revision token to prepare", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(preparationResponse("organize", "needs_clarification")));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    expect(prepare).toBeTypeOf("function");
    await prepare?.({
      mode: "organize",
      message: "限定在北京市",
      state: { state_id: "opaque", revision: 2 },
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toContain("/api/research/prepare");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      mode: "organize",
      message: "限定在北京市",
      state: { state_id: "opaque", revision: 2 },
    });
  });

  it("classifies a preparation state conflict as an explicit HTTP 409 error", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "研究准备状态已失效或冲突" }, 409));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    expect(prepare).toBeTypeOf("function");
    const error = await prepare?.({ mode: "qa", message: "继续", state: { state_id: "s", revision: 1 } }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
  });

  it("rejects malformed or mode-switched preparation responses", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(preparationResponse("organize", "ready")));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    const error = await prepare?.({ mode: "qa", message: "什么是数字经济" }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("malformed");
  });

  it("rejects ready preparation responses that still include options", async () => {
    const invalid = preparationResponse("qa", "ready");
    invalid.options = [{ id: "aspect", question: "继续选择", choices: [] }];
    vi.mocked(fetch).mockResolvedValue(jsonResponse(invalid));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    const error = await prepare?.({ mode: "qa", message: "什么是数字经济？" }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("malformed");
  });

  it("rejects empty server tokens", async () => {
    const invalid = preparationResponse("qa", "ready");
    invalid.state.state_id = "";
    vi.mocked(fetch).mockResolvedValue(jsonResponse(invalid));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    const error = await prepare?.({ mode: "qa", message: "什么是数字经济？" }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).kind).toBe("malformed");
  });

  it("rejects incomplete preparation state arrays", async () => {
    const invalid = preparationResponse("qa", "ready");
    delete (invalid.state as Partial<typeof invalid.state>).dimensions;
    vi.mocked(fetch).mockResolvedValue(jsonResponse(invalid));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    const error = await prepare?.({ mode: "qa", message: "什么是数字经济？" }).catch((e) => e);
    expect((error as ApiError).kind).toBe("malformed");
  });

  it("explains that a missing prepare route is unavailable without changing catalog 404 semantics", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "Not Found" }, 404));
    const prepare = (realClient as unknown as { prepare?: (request: unknown) => Promise<unknown> }).prepare;
    const error = await prepare?.({ mode: "qa", message: "什么是数字经济？" }).catch((e) => e);
    expect((error as ApiError).message).toBe("研究准备服务尚未部署。");
  });
});
