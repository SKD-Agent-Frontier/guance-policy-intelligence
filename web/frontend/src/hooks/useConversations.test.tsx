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
  researchClient: { prepare: vi.fn(), submit: vi.fn(), resolveCompare: vi.fn() },
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

  it.each([
    ["extract", "已收到你的提取需求。当前版本需要先指定 1 个政策对象。你可以前往文献目录查找政策编号，或直接在下方填写。"],
    ["compare_plans", "已收到你的多方案分析需求。当前版本需要先指定至少 2 个政策对象。你可以前往文献目录查找政策编号，或直接在下方填写。"],
  ] as const)("hands %s messages to the advanced draft without API calls", async (mode, guidance) => {
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode(mode));

    await act(async () => { await result.current.send("比较这些政策"); });

    expect(mockedClient.prepare).not.toHaveBeenCalled();
    expect(mockedClient.submit).not.toHaveBeenCalled();
    expect(result.current.active?.messages).toEqual([
      { id: expect.any(String), kind: "user", text: "比较这些政策" },
      { id: expect.any(String), kind: "advanced_guidance", text: guidance },
    ]);
    expect(result.current.active?.advancedDraft).toMatchObject({
      open: true,
      question: "比较这些政策",
      focusVersion: 1,
    });
  });


  it("automatically resolves a clear compare question and executes without a planid form", async () => {
    mockedClient.resolveCompare.mockResolvedValue({
      status: "ready",
      message: "已定位",
      targets: ["P-ZJ", "P-JS"],
      dimensions: ["发展目标"],
      policies: [
        { area: "浙江", planid: "P-ZJ", title: "浙江省数字经济发展十四五规划", fyp: "十四五", match: "title" },
        { area: "江苏", planid: "P-JS", title: "江苏省数字经济发展十四五规划", fyp: "十四五", match: "title" },
      ],
    });
    mockedClient.submit.mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));

    await act(async () => { await result.current.send("比较浙江和江苏十四五数字经济发展目标的异同"); });

    expect(mockedClient.resolveCompare).toHaveBeenCalledWith("比较浙江和江苏十四五数字经济发展目标的异同");
    expect(mockedClient.prepare).not.toHaveBeenCalled();
    expect(mockedClient.submit).toHaveBeenCalledWith({
      task_type: "compare",
      question: "比较浙江和江苏十四五数字经济发展目标的异同",
      targets: ["P-ZJ", "P-JS"],
      dimensions: ["发展目标"],
    }, undefined);
    expect(result.current.active?.advancedDraft.open).toBe(false);
    expect(result.current.active?.messages.some((item) => item.kind === "advanced_guidance")).toBe(false);
  });

  it("does not fabricate compare targets when the published catalog cannot resolve them", async () => {
    mockedClient.resolveCompare.mockResolvedValue({
      status: "needs_clarification",
      message: "没有找到匹配文献",
      targets: [],
      policies: [],
      dimensions: [],
    });
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));

    await act(async () => { await result.current.send("比较浙江和江苏十四五数字经济发展目标的异同"); });

    expect(mockedClient.submit).not.toHaveBeenCalled();
    expect(result.current.active?.messages.at(-1)).toEqual({
      id: expect.any(String), kind: "advanced_guidance", text: "没有找到匹配文献",
    });
    expect(result.current.active?.advancedDraft.open).toBe(true);
  });

  it("keeps each conversation's advanced draft and resets it when mode changes", () => {
    const { result } = renderHook(() => useConversations());
    const firstId = result.current.activeId;
    act(() => result.current.setMode("extract"));
    act(() => result.current.updateAdvancedDraft({ open: true, question: "原始问题", target: "P-1" }));
    act(() => result.current.openAdvanced());
    expect(result.current.active?.advancedDraft).toMatchObject({ question: "原始问题", target: "P-1", focusVersion: 1 });

    act(() => result.current.newConversation());
    expect(result.current.active?.advancedDraft).toEqual({
      open: false, question: "", target: "", targets: ["", ""], dimensions: [], customDimension: "", focusVersion: 0,
    });
    act(() => result.current.selectConversation(firstId));
    expect(result.current.active?.advancedDraft).toMatchObject({ question: "原始问题", target: "P-1", focusVersion: 1 });

    act(() => result.current.setMode("compare"));
    expect(result.current.active?.advancedDraft).toEqual({
      open: false, question: "", target: "", targets: ["", ""], dimensions: [], customDimension: "", focusVersion: 0,
    });
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
    expect(mockedClient.submit).toHaveBeenCalledTimes(1);
    expect(mockedClient.submit).toHaveBeenCalledWith(request, undefined);
  });

  it("does not hand off a message while an advanced run is already in progress", async () => {
    const pending = deferred<RunResult>();
    mockedClient.submit.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));
    const request = { task_type: "compare" as const, question: "比较政策", targets: ["A", "B"], dimensions: null };

    act(() => { void result.current.runAdvanced(request); });
    await waitFor(() => expect(mockedClient.submit).toHaveBeenCalledTimes(1));
    await act(async () => { await result.current.send("稍后比较"); });

    expect(result.current.active?.messages.some((item) => item.kind === "advanced_guidance")).toBe(false);
    expect(result.current.active?.advancedDraft.question).toBe("");
    await act(async () => { pending.resolve(RESULT); await pending.promise; });
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
  it("regenerates a completed answer with the original prepared request, replacing its old result", async () => {
    mockedClient.prepare.mockReset().mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockReset()
      .mockResolvedValueOnce({ ...RESULT, answer: "第一次回答" })
      .mockResolvedValueOnce({ ...RESULT, answer: "第二次回答" });
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("最初问题"); });
    const original = result.current.active?.messages.find((item) => item.kind === "result");
    expect(original).toBeDefined();

    await act(async () => { await result.current.regenerate(original!.id); });

    expect(mockedClient.prepare).toHaveBeenCalledTimes(1);
    expect(mockedClient.submit).toHaveBeenCalledTimes(2);
    expect(result.current.active?.messages.filter((item) => item.kind === "result")).toHaveLength(1);
    expect(result.current.active?.messages.at(-1)).toMatchObject({
      kind: "result", result: { answer: "第二次回答" },
    });
    expect(result.current.active?.messages.filter((item) => item.kind === "user")).toHaveLength(1);
  });

  it("editing an earlier user turn removes dependent messages and restarts preparation", async () => {
    mockedClient.prepare.mockReset().mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockReset().mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("第一轮问题"); });
    await act(async () => { await result.current.send("依赖第一轮的追问"); });
    const first = result.current.active?.messages.find((item) => item.kind === "user");
    expect(first).toBeDefined();

    await act(async () => { await result.current.editAndResend(first!.id, "修订后的第一轮问题"); });

    expect(result.current.active?.title).toBe("修订后的第一轮问题");
    expect(result.current.active?.messages.filter((item) => item.kind === "user")).toHaveLength(1);
    expect(result.current.active?.messages.find((item) => item.kind === "user")).toMatchObject({
      text: "修订后的第一轮问题",
    });
    expect(result.current.active?.messages.filter((item) => item.kind === "result")).toHaveLength(1);
    expect(mockedClient.prepare).toHaveBeenLastCalledWith({
      mode: "qa", message: "修订后的第一轮问题",
    });
  });

  it("regenerating an earlier answer discards subsequent turns, without preparing twice", async () => {
    mockedClient.prepare.mockReset().mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockReset().mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("问题一"); });
    const answer = result.current.active?.messages.find((item) => item.kind === "result");
    await act(async () => { await result.current.send("问题二"); });

    await act(async () => { await result.current.regenerate(answer!.id); });

    expect(mockedClient.prepare).toHaveBeenCalledTimes(2);
    expect(mockedClient.submit).toHaveBeenCalledTimes(3);
    expect(result.current.active?.messages.filter((item) => item.kind === "user")).toHaveLength(1);
    expect(result.current.active?.messages.filter((item) => item.kind === "result")).toHaveLength(1);
  });

  it("editing an advanced research message preserves its manually selected targets", async () => {
    mockedClient.submit.mockReset().mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));
    await act(async () => {
      await result.current.runAdvanced({
        task_type: "compare", question: "原问题", targets: ["P-1", "P-2"], dimensions: ["目标"],
      });
    });
    const turn = result.current.active?.messages.find((item) => item.kind === "user");
    await act(async () => { await result.current.editAndResend(turn!.id, "编辑后的比较问题"); });

    expect(mockedClient.submit).toHaveBeenLastCalledWith({
      task_type: "compare", question: "编辑后的比较问题",
      targets: ["P-1", "P-2"], dimensions: ["目标"],
    }, undefined);
    expect(mockedClient.submit).toHaveBeenCalledTimes(2);
    expect(result.current.active?.messages.filter((item) => item.kind === "user")).toHaveLength(1);
  });

  it("does not edit or regenerate conversation history during an in-flight operation", async () => {
    const pending = deferred<RunResult>();
    mockedClient.prepare.mockReset().mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockReset().mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());
    act(() => { void result.current.send("正在执行的问题"); });
    await waitFor(() => expect(result.current.active?.busy).toBe("running"));
    const original = result.current.active?.messages.find((item) => item.kind === "user");
    await act(async () => {
      await result.current.editAndResend(original!.id, "不应执行");
      await result.current.regenerate("nonexistent");
    });
    expect(result.current.active?.messages.filter((item) => item.kind === "user")).toHaveLength(1);
    expect(mockedClient.prepare).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(RESULT); await pending.promise; });
  });

});
