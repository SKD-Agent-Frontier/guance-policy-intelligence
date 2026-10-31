import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "./ConversationView";
import { createAdvancedDraft, type Conversation } from "../conversationTypes";
import type { PreparationResponse } from "../types";

const state: PreparationResponse["state"] = {
  state_id: "state-current",
  revision: 1,
  expires_at: "2026-10-30T12:00:00Z",
  mode: "qa",
  original_request: "数字经济",
  topic: "数字经济",
  time_scope: null,
  area: null,
  dimensions: [],
  confirmed_conditions: [{ id: "topic", text: "主题：数字经济" }],
  pending_conditions: [],
  completed_questions: [],
  clarification_count: 1,
  condition_statuses: [
    { condition_id: "period", status: "executable_filter", value: "十四五时期", reason: "可以传入执行过滤器" },
    { condition_id: "area", status: "request_only", value: "北京市", reason: "仅作为研究请求说明" },
  ],
};

function preparation(question: string, choices: string[], revision: number): PreparationResponse {
  return {
    status: "needs_clarification",
    mode: "qa",
    message: "请补充研究范围。",
    options: [{ id: `question-${revision}`, question, choices }],
    state: { ...state, revision },
    request: null,
    warnings: ["研究范围不保证发布日期精确过滤。"],
  };
}

function conversationFixture(): Conversation {
  const old = preparation("后端问题一", ["旧选项"], 0);
  const latest = preparation("后端动态问题二", ["后端动态选项二"], 1);
  return {
    id: "conversation-test",
    title: "数字经济",
    mode: "qa",
    messages: [
      { id: "user-1", kind: "user", text: "数字经济" },
      { id: "prep-1", kind: "preparation", response: old },
      { id: "user-2", kind: "user", text: "补充条件" },
      { id: "prep-2", kind: "preparation", response: latest },
    ],
    busy: "idle",
    preparation: latest,
    advancedDraft: createAdvancedDraft(),
  };
}

describe("ConversationView", () => {
  afterEach(cleanup);

  it("renders dynamic questions/options, disables stale options, and keeps warnings visible", () => {
    const onSend = vi.fn();
    render(<ConversationView conversation={conversationFixture()} onModeChange={vi.fn()} onSend={onSend} onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRestartPreparation={vi.fn()} onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "后端动态问题二" })).toBeInTheDocument();
    expect(screen.getAllByText("研究范围不保证发布日期精确过滤。")).toHaveLength(2);
    expect(screen.getAllByText("当前已确认")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "旧选项" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "后端动态选项二" }));
    expect(onSend).toHaveBeenCalledWith("后端动态选项二");

    fireEvent.click(screen.getAllByText("查看已确认研究条件与执行边界")[1]);
    expect(screen.getAllByText("可执行筛选")).toHaveLength(2);
    expect(screen.getAllByText(/具备后续执行能力，不代表已完成筛选/)).toHaveLength(2);
    expect(screen.getAllByText("仅作为请求条件")).toHaveLength(2);
  });
});


it("shows only a lightweight animated running status without a separate execution card", () => {
  const active: Conversation = {
    id: "running", title: "政策问答", mode: "qa", busy: "running",
    preparation: null, advancedDraft: { targets: "", dimensions: "" } as Conversation["advancedDraft"],
    messages: [
      { id: "u1", kind: "user", text: "数字经济政策有哪些？" },
      { id: "legacy", kind: "execution", text: "研究任务正在执行。" },
    ],
  };
  render(<ConversationView conversation={active} onModeChange={vi.fn()} onSend={vi.fn()}
    onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRestartPreparation={vi.fn()}
    onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);
  expect(screen.getByRole("status")).toHaveTextContent("正在研究中");
  expect(screen.queryByText("研究任务正在执行。")).not.toBeInTheDocument();
  expect(screen.queryByText("研究执行")).not.toBeInTheDocument();
});
