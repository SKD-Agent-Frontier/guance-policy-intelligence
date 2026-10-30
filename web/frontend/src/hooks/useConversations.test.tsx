import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, researchClient } from "../api/client";
import { useConversations } from "./useConversations";
import type { PreparationResponse, RunResult, TaskType } from "../types";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    kind: string;
    status: number | null;
    constructor(kind: string, message: string, status: number | null = null) {
      super(message);
      this.kind = kind;
      this.status = status;
    }
  },
  validatePreparationResponse: (response: unknown) => response,
  researchClient: { prepare: vi.fn(), submit: vi.fn() },
}));

const STATE = {
  state_id: "opaque-state",
  revision: 0,
  expires_at: "2026-10-30T12:00:00Z",
  mode: "qa" as TaskType,
  original_request: "数字经济",
  topic: "数字经济",
  time_scope: null,
  area: null,
  dimensions: [],
  confirmed_conditions: [],
  pending_conditions: [],
  completed_questions: [],
  clarification_count: 0,
  condition_statuses: [],
};

function preparation(
  status: PreparationResponse["status"],
  mode: TaskType = "qa",
  revision = 0,
): PreparationResponse {
  return {
    status,
    mode,
    message: status === "needs_clarification" ? "你关注哪个方面？" : "准备完成",
    options: status === "needs_clarification"
      ? [{ id: "aspect", question: "你关注哪个方面？", choices: ["政策", "发展"] }]
      : [],
    state: { ...STATE, mode, revision },
    request: status === "ready"
      ? { task_type: mode, question: "完整整理后的问题", targets: null, dimensions: ["政策工具"] }
      : null,
    warnings: [],
  };
}

const RESULT = {
  run_id: "run-1",
  session_id: "session-1",
  task_type: "qa",
  status: "completed",
  evidence_status: "verified",
  answer: "结果正文",
  claims: [],
  evidence_refs: [],
  dropped_chunk_ids: [],
  structured: null,
  warnings: [],
  error: null,
  route: null,
  meta: {},
} as unknown as RunResult;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const mockedClient = vi.mocked(researchClient);

describe("useConversations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("prepares without running, forwards only the revision token, then runs the ready request once", async () => {
    mockedClient.prepare
      .mockResolvedValueOnce(preparation("needs_clarification"))
      .mockResolvedValueOnce(preparation("ready", "qa", 1));
    mockedClient.submit.mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("数字经济"); });
    expect(mockedClient.submit).not.toHaveBeenCalled();
    expect(mockedClient.prepare).toHaveBeenNthCalledWith(1, { mode: "qa", message: "数字经济" });
    act(() => result.current.setMode("qa"));
    expect(result.current.active?.preparation?.state.state_id).toBe("opaque-state");

    await act(async () => { await result.current.send("产业政策"); });
    expect(mockedClient.prepare).toHaveBeenNthCalledWith(2, {
      mode: "qa",
      message: "产业政策",
      state: { state_id: "opaque-state", revision: 0 },
    });
    expect(mockedClient.submit).toHaveBeenCalledTimes(1);
    expect(mockedClient.submit).toHaveBeenCalledWith({
      task_type: "qa",
      question: "完整整理后的问题",
      targets: null,
      dimensions: ["政策工具"],
    }, undefined);
    expect(result.current.active?.messages.at(-1)).toEqual({
      id: expect.any(String),
      kind: "result",
      result: RESULT,
    });
  });

  it("does not run a blocked preparation request", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("blocked", "compare"));
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));

    await act(async () => { await result.current.send("比較政策"); });

    expect(mockedClient.submit).not.toHaveBeenCalled();
    expect(result.current.active?.preparation?.status).toBe("blocked");
  });

  it("labels QA blocked preparation as a scope block rather than an unsupported mode", async () => {
    mockedClient.prepare.mockResolvedValue({ ...preparation("blocked"), message: "全库能力无法保证" });
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("列出所有政策"); });

    expect(result.current.active?.messages).toContainEqual(expect.objectContaining({
      kind: "error",
      code: "PREPARATION_BLOCKED",
    }));
    expect(result.current.active?.messages).not.toContainEqual(expect.objectContaining({ code: "UNSUPPORTED_MODE" }));
  });

  it("runs advanced modes directly with explicit targets and without preparation", async () => {
    mockedClient.submit.mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));
    const request = {
      task_type: "compare" as const,
      question: "比较两项政策",
      targets: ["A", "B"],
      dimensions: null,
    };

    await act(async () => { await result.current.runAdvanced(request); });

    expect(mockedClient.prepare).not.toHaveBeenCalled();
    expect(mockedClient.submit).toHaveBeenCalledWith(request, undefined);
  });

  it("locks out a second send while preparation is pending", async () => {
    const pending = deferred<PreparationResponse>();
    mockedClient.prepare.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());

    act(() => {
      void result.current.send("第一次");
      void result.current.send("重复点击");
    });
    expect(mockedClient.prepare).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(preparation("needs_clarification")); await pending.promise; });
    await waitFor(() => expect(result.current.active?.busy).toBe("idle"));
  });

  it("ignores a late response after the user changes mode", async () => {
    const pending = deferred<PreparationResponse>();
    mockedClient.prepare.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());

    act(() => { void result.current.send("数字经济"); });
    act(() => result.current.setMode("organize"));
    await act(async () => { pending.resolve(preparation("needs_clarification")); await pending.promise; });

    expect(result.current.active?.mode).toBe("organize");
    expect(result.current.active?.preparation).toBeNull();
    expect(result.current.active?.busy).toBe("idle");
    expect(result.current.active?.messages.some((message) => message.kind === "preparation")).toBe(false);
  });

  it("keeps a late result on its original conversation after history navigation", async () => {
    const pending = deferred<RunResult>();
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());
    const firstId = result.current.activeId;

    act(() => { void result.current.send("什么是数字经济？"); });
    await waitFor(() => expect(mockedClient.submit).toHaveBeenCalledTimes(1));
    act(() => { result.current.newConversation(); });
    await act(async () => { pending.resolve(RESULT); await pending.promise; });

    expect(result.current.activeId).not.toBe(firstId);
    expect(result.current.sessions.find((session) => session.id === firstId)?.messages.at(-1)).toMatchObject({
      kind: "result",
      result: RESULT,
    });
  });

  it("preserves a 409 and requires explicit restart instead of replaying the stale token", async () => {
    mockedClient.prepare
      .mockResolvedValueOnce(preparation("needs_clarification"))
      .mockRejectedValueOnce(new ApiError("http", "研究准备状态已失效或冲突", 409));
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("数字经济"); });
    await act(async () => { await result.current.send("继续"); });
    expect(mockedClient.prepare).toHaveBeenCalledTimes(2);
    expect(result.current.active?.preparation?.state.revision).toBe(0);
    expect(result.current.active?.messages.at(-1)).toMatchObject({ kind: "error", code: "PREPARATION_CONFLICT" });

    await act(async () => { await result.current.retry(); });
    expect(mockedClient.prepare).toHaveBeenCalledTimes(2);
    act(() => result.current.restartPreparation());
    expect(result.current.active?.preparation).toBeNull();
    expect(result.current.active?.messages.at(-1)).toMatchObject({ kind: "error", code: "PREPARATION_RESTART" });
  });

  it("retries a failed execution without calling prepare again", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockRejectedValueOnce(new Error("temporary failure")).mockResolvedValueOnce(RESULT);
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("什么是数字经济？"); });
    expect(mockedClient.prepare).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.retry(); });

    expect(mockedClient.prepare).toHaveBeenCalledTimes(1);
    expect(mockedClient.submit).toHaveBeenCalledTimes(2);
  });

  it("classifies execution failures and evidence limits without losing the result", async () => {
    mockedClient.submit.mockResolvedValue({
      ...RESULT,
      status: "limit_reached",
      evidence_status: "none",
      error: { code: "MAX_TURNS", message: "达到执行轮数上限" },
    } as RunResult);
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("什么是数字经济？"); });

    expect(result.current.active?.messages).toContainEqual(expect.objectContaining({
      kind: "error",
      code: "MAX_TURNS",
    }));
    expect(result.current.active?.messages).toContainEqual(expect.objectContaining({
      kind: "result",
      result: expect.objectContaining({ status: "limit_reached", evidence_status: "none" }),
    }));
    await act(async () => { await result.current.retry(); });
    expect(mockedClient.submit).toHaveBeenCalledTimes(2);
  });

  it("keeps completed results with none evidence as results, not retryable errors", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockResolvedValue({ ...RESULT, answer: "未检索到某个维度", evidence_status: "none" } as RunResult);
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("什么是数字经济？"); });

    expect(result.current.active?.messages).toContainEqual(expect.objectContaining({
      kind: "result",
      result: expect.objectContaining({ status: "completed", evidence_status: "none" }),
    }));
    expect(result.current.active?.messages).not.toContainEqual(expect.objectContaining({
      kind: "error",
      code: "NO_EVIDENCE",
    }));
    await act(async () => { await result.current.retry(); });
    expect(mockedClient.submit).toHaveBeenCalledTimes(1);
  });
});
