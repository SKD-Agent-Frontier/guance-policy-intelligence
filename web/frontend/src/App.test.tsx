import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const mockState = vi.hoisted(() => ({
  conversation: {
    id: "conversation-1",
    title: "新对话",
    mode: "qa" as const,
    messages: [],
    busy: "idle" as const,
    preparation: null,
    advancedDraft: { open: false, question: "", target: "", targets: ["", ""], dimensions: [], customDimension: "", focusVersion: 0 },
  },
  setMode: vi.fn(),
  send: vi.fn(),
}));

vi.mock("./hooks/useConversations", () => ({
  useConversations: () => ({
    sessions: [mockState.conversation],
    activeId: mockState.conversation.id,
    active: mockState.conversation,
    selectConversation: vi.fn(),
    newConversation: vi.fn(),
    setMode: mockState.setMode,
    send: mockState.send,
    runAdvanced: vi.fn(),
    retry: vi.fn(),
    restartPreparation: vi.fn(),
    updateAdvancedDraft: vi.fn(),
    openAdvanced: vi.fn(),
  }),
}));

vi.mock("./components/PolicyCatalog", () => ({
  PolicyCatalog: () => <div aria-label="实际政策目录">政策目录占位</div>,
}));

describe("App navigation", () => {
  beforeEach(() => {
    mockState.setMode.mockClear();
    mockState.send.mockClear();
  });
  afterEach(cleanup);

  it("keeps the home icon and global sidebar available from home, workspace, and catalog", () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: "数据" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开侧栏" }));
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toHaveAttribute("title", "返回首页");

    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), { target: { value: "数字经济" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(screen.getByRole("main", { name: "研究工作区" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文献" }));
    expect(screen.getByLabelText("实际政策目录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();
  });
});
