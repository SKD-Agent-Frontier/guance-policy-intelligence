import { useCallback, useMemo, useRef, useState } from "react";

import { ApiError, researchClient, validatePreparationResponse } from "../api/client";
import type { DemoScenario } from "../data/demo";
import type { Conversation, ConversationMessage } from "../conversationTypes";
import type {
  PreparedRequest,
  ResearchPreparationRequest,
  ResearchRequest,
  RunResult,
  TaskType,
} from "../types";

interface OperationToken {
  id: number;
  generation: number;
}

type LastOperation =
  | { kind: "prepare"; request: ResearchPreparationRequest; scenario?: DemoScenario; failed: boolean; conflict: boolean }
  | { kind: "execution"; request: ResearchRequest; scenario?: DemoScenario; failed: boolean };

let nextConversationId = 1;
let nextMessageId = 1;
let nextOperationId = 1;

function newId(prefix: string): string {
  if (prefix === "conversation") return `${prefix}-${nextConversationId++}`;
  if (prefix === "operation") return `${prefix}-${nextOperationId++}`;
  return `${prefix}-${nextMessageId++}`;
}

function createConversation(mode: TaskType = "qa"): Conversation {
  return {
    id: newId("conversation"),
    title: "新对话",
    mode,
    messages: [],
    busy: "idle",
    preparation: null,
  };
}

function message(kind: ConversationMessage["kind"], fields: Record<string, unknown>): ConversationMessage {
  return { id: newId("message"), kind, ...fields } as ConversationMessage;
}

function apiError(error: unknown, operation: "prepare" | "execution"): { code: string; text: string; conflict: boolean } {
  if (error instanceof ApiError) {
    if (operation === "prepare" && error.status === 409) {
      return { code: "PREPARATION_CONFLICT", text: "研究准备状态已失效或冲突。请显式重新开始，并重新输入研究请求。", conflict: true };
    }
    if (error.status === 404 && operation === "prepare") {
      return { code: "PREPARATION_UNAVAILABLE", text: "研究准备服务尚未部署。", conflict: false };
    }
    if (error.status === 422 || error.status === 400) {
      return { code: "INVALID_PARAMETERS", text: error.message || "请求参数无效，请检查后重试。", conflict: false };
    }
    if (error.kind === "timeout") return { code: "TIMEOUT", text: error.message, conflict: false };
    if (error.kind === "network") return { code: "NETWORK", text: error.message, conflict: false };
    if (error.kind === "malformed") return { code: "RESPONSE_FORMAT", text: error.message, conflict: false };
    return { code: `HTTP_${error.status ?? "ERROR"}`, text: error.message, conflict: false };
  }
  if (error instanceof Error && error.name === "AbortError") {
    return { code: "CANCELLED", text: "请求已取消。", conflict: false };
  }
  if (error instanceof Error) return { code: "NETWORK", text: error.message, conflict: false };
  return { code: "UNKNOWN", text: "发生未知错误。", conflict: false };
}

function runResultError(result: RunResult): { code: string; text: string } | null {
  const error = result.error;
  if (!error && result.status === "completed") return null;
  if (error) {
    if (["MAX_TURNS", "RUN_TIMEOUT", "TOOL_BUDGET"].includes(error.code)) return { code: error.code, text: error.message };
    if (error.code === "OUTPUT_PARSE") return { code: "OUTPUT_PARSE", text: error.message };
    if (/NUMERIC|CITATION|VERIFY|IDENTITY/i.test(error.code)) {
      return { code: "NUMERIC_CITATION_VERIFICATION", text: error.message };
    }
    if (error.code === "RAG_EMPTY" || error.code === "NO_EVIDENCE") return { code: "RAG_EMPTY", text: error.message };
    return { code: error.code, text: error.message };
  }
  if (result.status === "limit_reached") {
    return { code: "LIMIT_REACHED", text: result.answer || "任务达到运行限制，返回内容可能不完整。" };
  }
  return { code: "EXECUTION_FAILED", text: result.answer || "研究任务未能完成。" };
}

function isAdvancedMode(mode: TaskType): boolean {
  return mode === "extract" || mode === "compare" || mode === "compare_plans";
}

export function useConversations() {
  const initial = useRef<Conversation | null>(null);
  if (!initial.current) initial.current = createConversation();
  const [sessions, setSessions] = useState<Conversation[]>([initial.current]);
  const [activeId, setActiveId] = useState(initial.current.id);
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  const generations = useRef(new Map<string, number>());
  const operations = useRef(new Map<string, number>());
  const lastOperations = useRef(new Map<string, LastOperation>());

  const updateSessions = useCallback((updater: (current: Conversation[]) => Conversation[]) => {
    const next = updater(sessionsRef.current);
    sessionsRef.current = next;
    setSessions(next);
  }, []);

  const updateConversation = useCallback((id: string, updater: (conversation: Conversation) => Conversation) => {
    updateSessions((current) => current.map((conversation) => conversation.id === id ? updater(conversation) : conversation));
  }, [updateSessions]);

  const appendMessage = useCallback((id: string, item: ConversationMessage) => {
    updateConversation(id, (conversation) => ({
      ...conversation,
      title: conversation.title === "新对话" && item.kind === "user" ? item.text.slice(0, 32) : conversation.title,
      messages: [...conversation.messages, item],
    }));
  }, [updateConversation]);

  const beginOperation = useCallback((id: string): OperationToken | null => {
    if (operations.current.has(id)) return null;
    const operationId = Number(newId("operation").split("-").at(-1));
    const generation = generations.current.get(id) ?? 0;
    operations.current.set(id, operationId);
    return { id: operationId, generation };
  }, []);

  const isCurrent = useCallback((conversationId: string, token: OperationToken) => (
    operations.current.get(conversationId) === token.id
      && (generations.current.get(conversationId) ?? 0) === token.generation
  ), []);

  const finishOperation = useCallback((conversationId: string, token: OperationToken) => {
    if (operations.current.get(conversationId) === token.id) operations.current.delete(conversationId);
  }, []);

  const setBusy = useCallback((id: string, busy: Conversation["busy"]) => {
    updateConversation(id, (conversation) => ({ ...conversation, busy }));
  }, [updateConversation]);

  const execute = useCallback(async (
    conversationId: string,
    request: ResearchRequest,
    scenario: DemoScenario | undefined,
    token: OperationToken,
  ) => {
    if (!isCurrent(conversationId, token)) return;
    setBusy(conversationId, "running");
    appendMessage(conversationId, message("execution", { text: "研究任务正在执行。" }));
    lastOperations.current.set(conversationId, { kind: "execution", request, scenario, failed: false });
    try {
      const result = await researchClient.submit(request, scenario);
      if (!isCurrent(conversationId, token)) return;
      appendMessage(conversationId, message("result", { result }));
      const resultError = runResultError(result);
      if (resultError) appendMessage(conversationId, message("error", resultError));
      lastOperations.current.set(conversationId, {
        kind: "execution",
        request,
        scenario,
        failed: result.status === "failed" || result.status === "limit_reached",
      });
      setBusy(conversationId, "idle");
    } catch (error) {
      if (!isCurrent(conversationId, token)) return;
      const mapped = apiError(error, "execution");
      appendMessage(conversationId, message("error", { code: mapped.code, text: mapped.text }));
      lastOperations.current.set(conversationId, { kind: "execution", request, scenario, failed: true });
      setBusy(conversationId, "idle");
    }
  }, [appendMessage, isCurrent, setBusy]);

  const prepareAndMaybeExecute = useCallback(async (
    conversationId: string,
    mode: TaskType,
    request: ResearchPreparationRequest,
    scenario: DemoScenario | undefined,
    token: OperationToken,
  ) => {
    if (!isCurrent(conversationId, token)) return;
    setBusy(conversationId, "preparing");
    lastOperations.current.set(conversationId, { kind: "prepare", request, scenario, failed: false, conflict: false });
    try {
      const rawResponse = await researchClient.prepare(request);
      if (!isCurrent(conversationId, token)) return;
      const response = validatePreparationResponse(rawResponse, mode);
      updateConversation(conversationId, (conversation) => ({ ...conversation, preparation: response }));
      appendMessage(conversationId, message("preparation", { response }));
      if (response.status === "blocked") {
        appendMessage(conversationId, message("error", {
          code: isAdvancedMode(mode) ? "UNSUPPORTED_MODE" : "PREPARATION_BLOCKED",
          text: response.message,
        }));
        setBusy(conversationId, "idle");
        return;
      }
      if (response.status !== "ready") {
        lastOperations.current.set(conversationId, { kind: "prepare", request, scenario, failed: false, conflict: false });
        setBusy(conversationId, "idle");
        return;
      }
      const preparedRequest = response.request as PreparedRequest;
      const executionRequest: ResearchRequest = preparedRequest;
      lastOperations.current.set(conversationId, { kind: "execution", request: executionRequest, scenario, failed: false });
      await execute(conversationId, executionRequest, scenario, token);
    } catch (error) {
      if (!isCurrent(conversationId, token)) return;
      const mapped = apiError(error, "prepare");
      appendMessage(conversationId, message("error", { code: mapped.code, text: mapped.text }));
      lastOperations.current.set(conversationId, { kind: "prepare", request, scenario, failed: true, conflict: mapped.conflict });
      setBusy(conversationId, "idle");
    }
  }, [appendMessage, execute, isCurrent, setBusy, updateConversation]);

  const send = useCallback(async (text: string, scenario?: DemoScenario) => {
    const conversationId = activeIdRef.current;
    const conversation = sessionsRef.current.find((item) => item.id === conversationId);
    if (!conversation || !text.trim()) return;
    if (conversation.messages.at(-1)?.kind === "error"
        && (conversation.messages.at(-1) as Extract<ConversationMessage, {kind: "error"}>).code === "PREPARATION_CONFLICT") return;
    const token = beginOperation(conversationId);
    if (!token) return;
    appendMessage(conversationId, message("user", { text }));
    const priorPreparation = conversation.preparation;
    const request: ResearchPreparationRequest = {
      mode: conversation.mode,
      message: text,
      ...(priorPreparation?.mode === conversation.mode
        ? { state: { state_id: priorPreparation.state.state_id, revision: priorPreparation.state.revision } }
        : {}),
    };
    try {
      await prepareAndMaybeExecute(conversationId, conversation.mode, request, scenario, token);
    } finally {
      finishOperation(conversationId, token);
    }
  }, [appendMessage, beginOperation, finishOperation, prepareAndMaybeExecute]);

  const runAdvanced = useCallback(async (request: ResearchRequest, scenario?: DemoScenario) => {
    const conversationId = activeIdRef.current;
    const conversation = sessionsRef.current.find((item) => item.id === conversationId);
    if (!conversation) return;
    const token = beginOperation(conversationId);
    if (!token) return;
    appendMessage(conversationId, message("user", { text: request.question }));
    if (!isAdvancedMode(request.task_type) || request.task_type !== conversation.mode) {
      appendMessage(conversationId, message("error", {
        code: "UNSUPPORTED_MODE",
        text: "请先选择对应的提取或比较模式，再提交高级请求。",
      }));
      finishOperation(conversationId, token);
      return;
    }
    try {
      await execute(conversationId, request, scenario, token);
    } finally {
      finishOperation(conversationId, token);
    }
  }, [appendMessage, beginOperation, execute, finishOperation]);

  const retry = useCallback(async () => {
    const conversationId = activeIdRef.current;
    const operation = lastOperations.current.get(conversationId);
    if (!operation?.failed || (operation.kind === "prepare" && operation.conflict)) return;
    const token = beginOperation(conversationId);
    if (!token) return;
    try {
      if (operation.kind === "execution") {
        await execute(conversationId, operation.request, operation.scenario, token);
      } else {
        const conversation = sessionsRef.current.find((item) => item.id === conversationId);
        if (conversation) await prepareAndMaybeExecute(conversationId, conversation.mode, operation.request, operation.scenario, token);
      }
    } finally {
      finishOperation(conversationId, token);
    }
  }, [beginOperation, execute, finishOperation, prepareAndMaybeExecute]);

  const restartPreparation = useCallback(() => {
    const conversationId = activeIdRef.current;
    const token = generations.current.get(conversationId) ?? 0;
    generations.current.set(conversationId, token + 1);
    operations.current.delete(conversationId);
    lastOperations.current.delete(conversationId);
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      preparation: null,
      busy: "idle",
      messages: [...conversation.messages, message("error", {
        code: "PREPARATION_RESTART",
        text: "已清除失效的准备状态。请重新输入研究请求以开始新的准备。",
      })],
    }));
  }, [updateConversation]);

  const selectConversation = useCallback((id: string) => {
    if (!sessionsRef.current.some((conversation) => conversation.id === id)) return;
    activeIdRef.current = id;
    setActiveId(id);
  }, []);

  const newConversation = useCallback(() => {
    const conversation = createConversation();
    updateSessions((current) => [conversation, ...current]);
    activeIdRef.current = conversation.id;
    setActiveId(conversation.id);
    return conversation.id;
  }, [updateSessions]);

  const setMode = useCallback((mode: TaskType) => {
    const conversationId = activeIdRef.current;
    const conversation = sessionsRef.current.find((item) => item.id === conversationId);
    if (!conversation || conversation.mode === mode) return;
    generations.current.set(conversationId, (generations.current.get(conversationId) ?? 0) + 1);
    operations.current.delete(conversationId);
    lastOperations.current.delete(conversationId);
    updateConversation(conversationId, (conversation) => ({
      ...conversation,
      mode,
      preparation: null,
      busy: "idle",
    }));
  }, [updateConversation]);

  const active = useMemo(() => sessions.find((conversation) => conversation.id === activeId) ?? null, [activeId, sessions]);

  return {
    sessions,
    activeId,
    active,
    selectConversation,
    newConversation,
    setMode,
    send,
    runAdvanced,
    retry,
    restartPreparation,
  };
}
