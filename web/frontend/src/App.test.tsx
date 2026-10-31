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
  newConversation: vi.fn(),
}));

vi.mock("./hooks/useConversations", () => ({
  useConversations: () => ({
    sessions: [mockState.conversation],
    activeId: mockState.conversation.id,
    active: mockState.conversation,
    selectConversation: vi.fn(),
    newConversation: mockState.newConversation,
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
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    mockState.setMode.mockClear();
    mockState.send.mockClear();
    mockState.newConversation.mockClear();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
  });

  it("expands the desktop sidebar from the home icon", () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: "数据" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toHaveAttribute("title", "返回首页");
    expect(screen.getByText("最近研究")).toBeInTheDocument();
  });

  it("keeps the home icon available from workspace and catalog", () => {
    render(<App />);
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), { target: { value: "数字经济" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(screen.getByRole("main", { name: "研究工作区" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "文献" }));
    expect(screen.getByLabelText("实际政策目录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回首页" })).toBeInTheDocument();
  });

  it("shows the dedicated history icon while collapsed and opens the history section", () => {
    render(<App />);
    const sidebar = screen.getByRole("complementary", { name: "全局侧栏" });
    expect(sidebar).toHaveClass("is-collapsed");
    expect(screen.queryByText("最近研究")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "历史记录" }));
    expect(sidebar).not.toHaveClass("is-collapsed");
    expect(screen.getByText("最近研究")).toBeInTheDocument();
    expect(screen.getByTitle("新对话")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "历史记录" })).toBeInTheDocument();
  });

  it("expands recent conversations from the official logo without changing page", () => {
    render(<App />);
    expect(screen.queryByText("最近研究")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(screen.getByText("最近研究")).toBeInTheDocument();
    expect(screen.getByTitle("新对话")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开侧栏" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "展开最近研究" })).not.toBeInTheDocument();
  });

  it("opens the mobile drawer from a collapsed nav icon and keeps its action", () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    render(<App />);
    const sidebar = screen.getByRole("complementary", { name: "全局侧栏" });
    expect(sidebar).toHaveClass("is-collapsed");
    fireEvent.click(screen.getByRole("button", { name: "新建研究" }));
    expect(screen.getByRole("button", { name: "关闭侧栏" })).toBeInTheDocument();
    expect(sidebar).toHaveClass("is-mobile-open");
    expect(screen.getByText("最近研究")).toBeInTheDocument();
    expect(mockState.newConversation).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "关闭侧栏" }));
    expect(sidebar).toHaveClass("is-collapsed");
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(screen.getByRole("button", { name: "关闭侧栏" })).toBeInTheDocument();
    expect(screen.getByText("最近研究")).toBeInTheDocument();
  });

  it("hides the top brand and menu on home while retaining accessible settings", () => {
    render(<App />);
    expect(screen.queryByRole("button", { name: "管策智研首页" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "打开侧栏" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    const settings = screen.getByRole("button", { name: "身份与环境设置" });
    expect(settings).toBeInTheDocument();
    expect(settings).toHaveAccessibleName("身份与环境设置");
    expect(settings).not.toHaveTextContent("学号");
    settings.focus();
    expect(settings).toHaveFocus();
  });
});
