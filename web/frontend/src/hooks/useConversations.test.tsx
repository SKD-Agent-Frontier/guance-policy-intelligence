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
  isRealMode: true,
  researchClient: { prepare: vi.fn(), submit: vi.fn(), resolveCompare: vi.fn(), resolveResearch: vi.fn() },
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

  it("renames a conversation and does not resurrect it when an in-flight request completes after deletion", async () => {
    const pending = deferred<PreparationResponse>();
    mockedClient.prepare.mockReturnValue(pending.promise);
    const { result } = renderHook(() => useConversations());
    const id = result.current.activeId;
    let request!: Promise<void>;
    act(() => { request = result.current.send("数字经济"); });
    act(() => result.current.renameConversation(id, "政策研究"));
    expect(result.current.sessions.find((item) => item.id === id)?.title).toBe("政策研究");
    act(() => result.current.deleteConversation(id));
    await act(async () => { pending.resolve(preparation("needs_clarification")); await request; });
    expect(result.current.sessions.some((item) => item.id === id)).toBe(false);
  });

  it("reuses one unpredictable Agent session per conversation but not across conversations", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("第一问"); });
    await act(async () => { await result.current.send("那它的后续措施呢？"); });
    const first = mockedClient.submit.mock.calls[0][0].session_id;
    const second = mockedClient.submit.mock.calls[1][0].session_id;
    expect(first).toMatch(/^guance-[a-f0-9-]{32,36}$/);
    expect(second).toBe(first);
    act(() => { result.current.newConversation(); });
    await act(async () => { await result.current.send("独立的新问题"); });
    const third = mockedClient.submit.mock.calls[2][0].session_id;
    expect(third).toMatch(/^guance-[a-f0-9-]{32,36}$/);
    expect(third).not.toBe(first);
  });

  it("prepares without running, forwards only the revision token, then runs the ready request once", async () => {
    mockedClient.prepare
      .mockResolvedValueOnce(preparation("needs_clarification"))
      .mockResolvedValueOnce(preparation("ready", "qa", 1));
    mockedClient.submit.mockResolvedValue(RESULT);
    const { result } = renderHook(() => useConversations());

    await act(async () => { await result.current.send("数字经济"); });
    expect(mockedClient.submit).not.toHaveBeenCalled();
    expect(mockedClient.prepare).toHaveBeenNthCalledWith(1, { mode: "qa", message: "数字经济", autonomous: true });
    act(() => result.current.setMode("qa"));
    expect(result.current.active?.preparation?.state.state_id).toBe("opaque-state");

    await act(async () => { await result.current.send("产业政策"); });
    expect(mockedClient.prepare).toHaveBeenNthCalledWith(2, {
      mode: "qa",
      message: "产业政策",
      autonomous: true,
      state: { state_id: "opaque-state", revision: 0 },
    });
    expect(mockedClient.submit).toHaveBeenCalledTimes(1);
    expect(mockedClient.submit).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "qa",
      question: "完整整理后的问题",
      targets: null,
      dimensions: ["政策工具"],
    }), undefined);
    expect(result.current.active?.messages.at(-1)).toEqual({
      id: expect.any(String),
      kind: "result",
      result: RESULT,
    });
  });

  it("summarizes the first question and keeps that title across follow-ups and history", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("blocked"));
    const { result } = renderHook(() => useConversations());
    const firstId = result.current.activeId;
    const question = "密云区在‘十一五’规划期间所处的发展环境总体如何？其中的机遇和挑战有哪些？";

    await act(async () => { await result.current.send(question); });
    expect(result.current.active?.title).toBe("密云区“十一五”发展环境分析");
    expect(result.current.active?.messages.find((entry) => entry.kind === "user")).toMatchObject({ text: question });

    await act(async () => { await result.current.send("请进一步分析面临的挑战"); });
    expect(result.current.active?.title).toBe("密云区“十一五”发展环境分析");

    act(() => result.current.newConversation());
    const secondId = result.current.activeId;
    await act(async () => {
      await result.current.send("我想了解沧州市在十三五期间经济社会发展取得了哪些重大成就");
    });
    expect(result.current.active?.title).toBe("沧州市“十三五”经济社会发展成就");
    act(() => result.current.selectConversation(firstId));
    expect(result.current.active?.title).toBe("密云区“十一五”发展环境分析");
    act(() => result.current.selectConversation(secondId));
    expect(result.current.active?.title).toBe("沧州市“十三五”经济社会发展成就");
  });

  it("updates the title only when editing the first user turn", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("blocked"));
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("密云区十一五发展环境分析有哪些重点？"); });
    const first = result.current.active?.messages.find((entry) => entry.kind === "user");
    expect(result.current.active?.title).toBe("密云区“十一五”发展环境分析");
    await act(async () => {
      await result.current.editAndResend(first!.id, "沧州市十三五经济社会发展取得了哪些重要成就？");
    });
    expect(result.current.active?.title).toBe("沧州市“十三五”经济社会发展成就");
  });

  it.each(["extract", "compare", "compare_plans"] as const)(
    "%s sends natural-language goals directly to the backend planner",
    async (mode) => {
      mockedClient.submit.mockResolvedValue(RESULT);
      const { result } = renderHook(() => useConversations());
      act(() => result.current.setMode(mode));
      await act(async () => { await result.current.send("分析这项研究目标"); });
      expect(mockedClient.resolveResearch).not.toHaveBeenCalled();
      expect(mockedClient.prepare).not.toHaveBeenCalled();
      expect(mockedClient.submit).toHaveBeenCalledWith(expect.objectContaining({
        task_type: mode, question: "分析这项研究目标",
      }), undefined);
      expect(result.current.active?.messages.at(-1)?.kind).toBe("result");
      expect(result.current.active?.advancedDraft.open).toBe(false);
    },
  );

  it("lets the backend return insufficient evidence instead of frontend blocking execution", async () => {
    mockedClient.submit.mockResolvedValue({
      ...RESULT, status: "completed", evidence_status: "insufficient",
      answer: "发布库缺少有效的跨地市对照文献，不能形成可靠结论。",
    });
    const { result } = renderHook(() => useConversations());
    act(() => result.current.setMode("compare"));
    await act(async () => { await result.current.send("比较衡水市与河北省其他地市十三五产业政策"); });
    expect(mockedClient.submit).toHaveBeenCalledTimes(1);
    expect(result.current.active?.messages.at(-1)).toMatchObject({
      kind: "result", result: { evidence_status: "insufficient" },
    });
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
    expect(mockedClient.submit).toHaveBeenCalledWith(expect.objectContaining(request), undefined);
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

    // One failed result carries the original error code; no duplicate error message.
    expect(result.current.active?.messages.filter((item) => item.kind === "error")).toHaveLength(0);
    expect(result.current.active?.messages).toContainEqual(expect.objectContaining({
      kind: "result",
      result: expect.objectContaining({
        status: "limit_reached", evidence_status: "none",
        error: { code: "MAX_TURNS", message: "达到执行轮数上限" },
      }),
    }));
    await act(async () => { await result.current.retry(); });
    expect(mockedClient.submit).toHaveBeenCalledTimes(2);
  });

  it("keeps OUTPUT_PARSE diagnostics inside one failed result message", async () => {
    mockedClient.prepare.mockResolvedValue(preparation("ready"));
    mockedClient.submit.mockResolvedValue({
      ...RESULT, status: "failed", evidence_status: "none",
      error: { code: "OUTPUT_PARSE", message: "模型输出无法解析为约定结构" },
    } as RunResult);
    const { result } = renderHook(() => useConversations());
    await act(async () => { await result.current.send("低空经济"); });
    const messages = result.current.active?.messages ?? [];
    expect(messages.filter((entry) => entry.kind === "result")).toHaveLength(1);
    expect(messages.filter((entry) => entry.kind === "error")).toHaveLength(0);
    expect(messages.at(-1)).toMatchObject({
      kind: "result",
      result: { status: "failed", error: { code: "OUTPUT_PARSE", message: "模型输出无法解析为约定结构" } },
    });
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
      mode: "qa", message: "修订后的第一轮问题", autonomous: true,
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

    expect(mockedClient.submit).toHaveBeenLastCalledWith(expect.objectContaining({
      task_type: "compare", question: "编辑后的比较问题",
      targets: ["P-1", "P-2"], dimensions: ["目标"],
    }), undefined);
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
