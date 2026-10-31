import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "./ConversationView";
import { createAdvancedDraft, type Conversation } from "../conversationTypes";
import type { EvidenceRef, PreparationResponse, RunResult } from "../types";
import { catalogClient } from "../api/catalogClient";

vi.mock("../api/catalogClient", () => ({ catalogClient: { detail: vi.fn(), content: vi.fn() } }));

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

  it("keeps clarification choices usable but hides explanatory metadata beneath them", () => {
    const onSend = vi.fn();
    render(<ConversationView conversation={conversationFixture()} onModeChange={vi.fn()} onSend={onSend} onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRestartPreparation={vi.fn()} onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "后端动态问题二" })).toBeInTheDocument();
    // Conversation bubbles no longer repeat speaker names above their content.
    expect(screen.queryByText("你", { selector: ".message-role" })).not.toBeInTheDocument();
    expect(screen.queryByText("研究助手", { selector: ".message-role" })).not.toBeInTheDocument();
    expect(document.querySelectorAll(".message-user .message-role, .message-preparation .message-role")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "旧选项" })).toBeDisabled();
    expect(screen.queryByText(/这是历史准备消息/)).not.toBeInTheDocument();
    expect(screen.queryByText("当前已确认")).not.toBeInTheDocument();
    expect(screen.queryByText("查看已确认研究条件与执行边界")).not.toBeInTheDocument();
    expect(screen.queryByText("研究范围不保证发布日期精确过滤。")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "后端动态选项二" }));
    expect(onSend).toHaveBeenCalledWith("后端动态选项二");
  });
});


it("shows only a lightweight animated running status without a separate execution card", () => {
  const active: Conversation = {
    id: "running", title: "政策问答", mode: "qa", busy: "running",
    preparation: null, advancedDraft: createAdvancedDraft(),
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

const source: EvidenceRef = {
  planid: "P-1", chunk_id: "chunk-1", parent_id: null,
  title: "海西州数字经济发展规划", area: "海西州", fyp: "十四五",
  admin_level: "prefecture", plantype_label: "专项规划", section_idx: 0,
  section_path: "第一章", char_start: 0, char_end: 12,
  chunk_char_start: 0, chunk_char_end: 12, truncated: false,
  source: "kb_read", observed_ranges: [[0, 12]], complete: true, out_of_section: false,
  document_id: null, document_version_id: null, generation_id: "gen-1",
  source_hash: null, section_id: null, section_char_start: null,
  section_char_end: null, offset_basis: null, offset_unit: null,
  body_origin: null, contract_version: null, quality_warnings: [],
};
const completed: RunResult = {
  run_id: "run-1", session_id: "session-1", task_type: "qa",
  status: "completed", evidence_status: "insufficient",
  answer: "当前结论。", claims: [{ text: "相关结论", chunk_ids: ["chunk-1"],
    quotes: ["可追溯原文"], dimension: null, anchors: [] }],
  evidence_refs: [source], dropped_chunk_ids: [], error: null, meta: {},
  warnings: ["存在未核验引用"], structured: null, route: null,
};
function completedConversation(): Conversation {
  return {
    ...conversationFixture(), busy: "idle", preparation: null,
    messages: [
      { id: "u1", kind: "user", text: "研究问题" },
      { id: "ready", kind: "preparation", response: {
        ...preparation("准备完成", [], 2), status: "ready",
        message: "研究请求已准备完成。", options: [], request: null,
      } },
      { id: "result1", kind: "result", result: completed },
    ],
  };
}
function renderCompleted() {
  return render(<ConversationView conversation={completedConversation()} onModeChange={vi.fn()} onSend={vi.fn()}
    onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRestartPreparation={vi.fn()}
    onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);
}

describe("conversation simplification and original source reader", () => {
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.clearAllMocks(); });

  it("hides completed preparation, mode selector, local note, and status prelude", () => {
    renderCompleted();
    expect(screen.getByRole("heading", { name: "研究结论" })).toBeVisible();
    expect(document.querySelector(".results-content--qa .results-kicker")).toBeNull();
    expect(screen.queryByText("结论与本次检索到的原文逐条关联。")).not.toBeInTheDocument();
    expect(screen.getByText("依据与引句")).toBeInTheDocument();
    expect(screen.getByText("1 条结论")).toBeInTheDocument();
    expect(document.querySelector(".qa-claims .results-section-title > span:last-child")).toHaveTextContent("1 条结论");
    expect(screen.queryByText("研究请求已准备完成。")).not.toBeInTheDocument();
    expect(screen.queryByText("查看已确认研究条件与执行边界")).not.toBeInTheDocument();
    expect(screen.queryByText("正式研究结果")).not.toBeInTheDocument();
    expect(screen.queryByText("研究完成")).not.toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "研究模式" })).not.toBeInTheDocument();
    expect(screen.queryByText(/对话记录和准备状态仅保存在当前前端会话/)).not.toBeInTheDocument();
    expect(screen.getByText("研究提示与核验信息")).toBeInTheDocument();
  });

  it("previews source on hover and opens real published original in standalone view", async () => {
    const dataset = {
      generation_id: "gen-1", revision: 1, total_documents: 1,
      partial: true, source: "current_published_frozen_release",
      region_version: null, region_mapping_version: null,
    };
    vi.mocked(catalogClient.detail).mockResolvedValue({
      dataset, policy: { planid: "P-1", title: source.title, area: source.area,
        fyp: source.fyp, plantype_label: source.plantype_label,
        body_chars: 10, sections: [{ index: 0, level: 1, path: "第一章", start: 0, end: 10 }] },
    } as never);
    vi.mocked(catalogClient.content).mockResolvedValue({
      dataset, planid: "P-1", title: source.title,
      offset: 0, limit: 4000, total_chars: 10, has_more: false, text: "真实政策正文",
    } as never);
    renderCompleted();
    const original = screen.getByRole("button", { name: "原文 1" });
    vi.useFakeTimers();
    fireEvent.mouseOver(original);
    await act(async () => { vi.advanceTimersByTime(200); });
    expect(screen.getByLabelText("原文预览")).toHaveTextContent("可追溯原文");
    expect(catalogClient.content).not.toHaveBeenCalled();
    vi.useRealTimers();
    fireEvent.click(original);
    expect(screen.getByRole("main", { name: "政策原文阅读" })).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "研究工作区" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("真实政策正文")).toBeInTheDocument());
    expect(catalogClient.detail).toHaveBeenCalledWith("P-1", expect.any(AbortSignal));
    expect(catalogClient.content).toHaveBeenCalledWith("P-1", 0, 4000, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "返回研究对话" }));
    expect(screen.getByRole("heading", { name: "研究结论" })).toBeInTheDocument();
  });
  it("shows copy and regenerate beneath the agent answer, and copy and edit beneath user text", () => {
    const onRegenerate = vi.fn();
    const onEditAndResend = vi.fn();
    render(<ConversationView conversation={completedConversation()} onModeChange={vi.fn()} onSend={vi.fn()}
      onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRegenerate={onRegenerate}
      onEditAndResend={onEditAndResend} onRestartPreparation={vi.fn()}
      onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);

    expect(screen.getAllByRole("button", { name: "复制消息" })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onRegenerate).toHaveBeenCalledWith("result1");
    fireEvent.click(screen.getByRole("button", { name: "编辑消息" }));
    const editor = screen.getByRole("textbox", { name: "编辑消息内容" });
    expect(editor).toHaveValue("研究问题");
    fireEvent.change(editor, { target: { value: "修改后的研究问题" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并发送" }));
    expect(onEditAndResend).toHaveBeenCalledWith("u1", "修改后的研究问题");
  });

  it("copies assistant rendered content, not the message-action labels", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const previous = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    try {
      renderCompleted();
      const copies = screen.getAllByRole("button", { name: "复制消息" });
      fireEvent.click(copies[1]);
      await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
      const copied = writeText.mock.calls[0][0] as string;
      expect(copied).toContain("当前结论");
      expect(copied).not.toContain("重新生成");
      expect(screen.getByRole("button", { name: "已复制" })).toBeInTheDocument();
    } finally {
      if (previous) Object.defineProperty(navigator, "clipboard", previous);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  });

  it("can cancel an inline edit without changing the original message", () => {
    const onEdit = vi.fn();
    render(<ConversationView conversation={completedConversation()} onModeChange={vi.fn()} onSend={vi.fn()}
      onAdvancedSubmit={vi.fn()} onRetry={vi.fn()} onRegenerate={vi.fn()}
      onEditAndResend={onEdit} onRestartPreparation={vi.fn()}
      onAdvancedDraftChange={vi.fn()} onSpecifyPolicies={vi.fn()} onOpenCatalog={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑消息" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑消息内容" }), {
      target: { value: "未提交草稿" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.getByText("研究问题")).toBeInTheDocument();
  });

});
