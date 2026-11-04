import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { catalogClient } from "../api/catalogClient";
import { promptTemplates } from "../promptTemplates";
import { ResearchForm } from "./ResearchForm";
import type { TaskType } from "../types";

vi.mock("../api/catalogClient", () => ({
  catalogClient: { list: vi.fn() },
}));

function ControlledForm({
  onSend = vi.fn(),
  onAdvancedSubmit = vi.fn(),
}: {
  onSend?: (message: string) => void;
  onAdvancedSubmit?: (request: unknown) => void;
}) {
  const [mode, setMode] = useState<TaskType>("qa");
  const [value, setValue] = useState("");
  const modes: { id: TaskType; label: string }[] = [
    { id: "qa", label: "政策问答" },
    { id: "organize", label: "文献梳理" },
    { id: "extract", label: "要素提取" },
    { id: "compare", label: "政策比较" },
    { id: "compare_plans", label: "多方案分析" },
  ];
  const changeMode = (next: TaskType) => {
    if (next !== mode && promptTemplates[mode].some(item => item.prompt === value)) setValue("");
    setMode(next);
  };
  return <div>
    <div role="group" aria-label="研究模式">
      {modes.map(({ id, label }) => (
        <button type="button" key={id} aria-label={`选择${label}`} aria-pressed={mode === id} onClick={() => changeMode(id)}>{label}</button>
      ))}
    </div>
    <ResearchForm mode={mode} value={value} busy={false}
      onChange={setValue} onModeChange={setMode} onSend={onSend} onAdvancedSubmit={onAdvancedSubmit as never} />;
  </div>;
}

function selectMode(label: string) {
  fireEvent.click(screen.getByRole("button", { name: `选择${label}` }));
}

function openMenu() {
  fireEvent.click(screen.getByRole("button", { name: "更多功能" }));
}

function openTemplates() {
  fireEvent.click(screen.getByRole("button", { name: "更多功能" }));
  fireEvent.click(screen.getByRole("button", { name: "指令模板" }));
}

describe("ResearchForm plus menu", () => {
  afterEach(cleanup);
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(catalogClient.list).mockResolvedValue({
      dataset: {} as never,
      query: {} as never,
      matched_documents: 1,
      page: 1,
      page_size: 8,
      total_pages: 1,
      items: [{
        planid: "18563",
        title: "江西省十四五规划纲要",
        area: "江西省",
        fyp: "十四五",
        plantype_label: "发展规划",
        matches: [],
      }],
    });
  });

  it("uses visible mode tabs and keeps the plus menu for citations and templates", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    openMenu();
    expect(screen.getByRole("region", { name: "更多功能菜单" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "引用文献" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "指令模板" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /研究模式/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "引用文献" }));
    expect(screen.getByRole("region", { name: "引用文献选择" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭更多功能" }));
    expect(screen.queryByRole("region", { name: "引用文献选择" })).not.toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("lets users select library documents and passes citations with the request", async () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "引用文献" }));
    const result = await screen.findByRole("button", { name: /江西省十四五规划纲要/ });
    fireEvent.click(result);
    fireEvent.click(screen.getByRole("button", { name: "关闭更多功能" }));
    expect(screen.getByRole("group", { name: "已引用文献" })).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "江西数字经济发展要求是什么" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith(
      "江西数字经济发展要求是什么\n\n已引用文献：\n- planid=18563 《江西省十四五规划纲要》",
      expect.arrayContaining([expect.objectContaining({ planid: "18563" })]),
    );
    expect(screen.queryByRole("group", { name: "已引用文献" })).not.toBeInTheDocument();
  });

  it("passes selected documents as explicit targets for comparison modes", async () => {
    const onAdvancedSubmit = vi.fn();
    render(<ControlledForm onAdvancedSubmit={onAdvancedSubmit} />);
    selectMode("政策比较");
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "引用文献" }));
    const result = await screen.findByRole("button", { name: /江西省十四五规划纲要/ });
    fireEvent.click(result);
    fireEvent.click(screen.getByRole("button", { name: "关闭更多功能" }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "比较数字经济发展目标" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onAdvancedSubmit).toHaveBeenCalledWith(expect.objectContaining({
      task_type: "compare",
      question: "比较数字经济发展目标",
      targets: ["18563"],
    }));
  });

  it("searches the catalog before showing citation results", async () => {
    render(<ControlledForm />);
    openMenu();
    fireEvent.click(screen.getByRole("button", { name: "引用文献" }));
    // eslint-disable-next-line no-console
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索引用文献" }), {
      target: { value: "数字经济" },
    });
    await waitFor(() => expect(catalogClient.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "数字经济", page_size: 8 }),
      expect.any(AbortSignal),
    ));
    await screen.findByRole("button", { name: /江西省十四五规划纲要/ });
  });

  it("shows one compact plus entry and keeps mode switching outside nested menus", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    expect(screen.getByRole("button", { name: "更多功能" })).toHaveAttribute("aria-expanded", "false");
    selectMode("政策比较");
    expect(screen.getAllByText("政策比较").length).toBeGreaterThan(0);
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "恢复默认模式" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复默认模式" }));
    expect(screen.queryByRole("button", { name: "恢复默认模式" })).not.toBeInTheDocument();
  });

  it("keeps instruction templates in the same plus menu without changing the mode", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    openTemplates();
    expect(screen.getByRole("region", { name: "指令模板列表" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^政策比较$/ }));
    fireEvent.click(screen.getByRole("button", { name: /发展目标比较/ }));
    const input = screen.getByRole("textbox", { name: "研究需求" });
    expect(input).toHaveValue("比较浙江和江苏十四五数字经济发展目标的异同");
    expect(input).toHaveFocus();
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
    selectMode("政策问答");
    expect(screen.getByRole("button", { name: "选择政策问答" })).toHaveAttribute("aria-pressed", "true");
    expect(onSend).not.toHaveBeenCalled();
    selectMode("政策比较");
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "修改后比较政策支持措施" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith("修改后比较政策支持措施", undefined);
  });

  it("preserves edited user text when changing mode", () => {
    render(<ControlledForm />);
    openTemplates();
    fireEvent.click(screen.getByRole("button", { name: /政策事实查询/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "我手工修改的研究要求" },
    });
    selectMode("要素提取");
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("我手工修改的研究要求");
    expect(screen.queryByRole("textbox", { name: /政策编号|研究对象编号/ })).not.toBeInTheDocument();
  });

  it("supports nested navigation, Escape and outside click", () => {
    render(<ControlledForm />);
    openTemplates();
    fireEvent.click(screen.getByRole("button", { name: "返回更多功能" }));
    expect(screen.getByRole("region", { name: "更多功能菜单" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "更多功能菜单" })).not.toBeInTheDocument();
    openMenu();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("region", { name: "研究模式选择" })).not.toBeInTheDocument();
  });

  it("keeps the same plus control in the conversation composer", () => {
    render(<ControlledForm />);
    expect(screen.getByRole("button", { name: "更多功能" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "指令模板" })).not.toBeInTheDocument();
  });

  it("grows for long text and respects Enter, Shift+Enter, and IME composition", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    const textarea = screen.getByRole("textbox", { name: "研究需求" });
    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 560 });
    fireEvent.change(textarea, { target: { value: "长文本".repeat(1200) } });
    expect(textarea).toHaveStyle({ height: "200px", overflowY: "auto" });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("counts Unicode code points without truncation and blocks over-limit sends", () => {
    render(<ControlledForm />);
    const textarea = screen.getByRole("textbox", { name: "研究需求" });
    const send = screen.getByRole("button", { name: "发送消息" });
    fireEvent.change(textarea, { target: { value: "😀".repeat(4000) } });
    expect(screen.queryByText("4000/4000")).not.toBeInTheDocument();
    expect(send).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "😀".repeat(4001) } });
    expect(send).toBeDisabled();
  });
});
