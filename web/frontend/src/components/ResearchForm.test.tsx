import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchForm } from "./ResearchForm";
import type { TaskType } from "../types";
import type { AdvancedDraft } from "../conversationTypes";

const draft = (overrides: Partial<AdvancedDraft> = {}): AdvancedDraft => ({
  open: false, question: "", target: "", targets: ["", ""], dimensions: [], customDimension: "", focusVersion: 0,
  ...overrides,
});

function ControlledForm({
  onSend = vi.fn(),
  enableTemplates = false,
}: {
  onSend?: (message: string) => void;
  enableTemplates?: boolean;
}) {
  const [mode, setMode] = useState<TaskType>("qa");
  const [value, setValue] = useState("");
  const [advancedDraft, setAdvancedDraft] = useState(draft());
  return (
    <ResearchForm
      mode={mode}
      value={value}
      busy={false}
      onChange={setValue}
      onModeChange={setMode}
      onSend={() => onSend(value)}
      enableTemplates={enableTemplates}
      advancedDraft={advancedDraft}
      onAdvancedDraftChange={(patch) => setAdvancedDraft((current) => ({ ...current, ...patch }))}
    />
  );
}

describe("ResearchForm", () => {
  afterEach(cleanup);
  it("offers all five modes and does not send when the user only changes mode", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);

    for (const mode of ["政策问答", "要素提取", "文献梳理", "政策比较", "多方案分析"])
      expect(screen.getByRole("tab", { name: mode })).toBeInTheDocument();
    expect(document.querySelector(".active-mode-label")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "文献梳理" }));
    expect(onSend).not.toHaveBeenCalled();
  });


  it("uses a homepage-only template icon with the exact hover label and never sends on selection", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} enableTemplates />);
    const trigger = screen.getByRole("button", { name: "指令模板" });
    expect(trigger).toHaveAccessibleName("指令模板");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "政策比较" }));
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("region", { name: "指令模板列表" })).toBeInTheDocument();
    expect(screen.getByText("政策比较 · 指令模板")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发展目标比较/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /政策工具比较/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /实施路径比较/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /发展目标比较/ }));
    const input = screen.getByRole("textbox", { name: "研究需求" });
    expect(input).toHaveValue("比较浙江和江苏十四五数字经济发展目标的异同");
    expect(input).toHaveFocus();
    expect(screen.getByRole("button", { name: /发展目标比较/ })).toHaveAttribute("aria-pressed", "true");
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input, { target: { value: "比较浙江和北京十四五数字经济发展目标的异同" } });
    expect(screen.getByRole("button", { name: /发展目标比较/ })).toHaveAttribute("aria-pressed", "false");
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith("比较浙江和北京十四五数字经济发展目标的异同");
  });

  it("shows three templates for the selected mode and does not carry unedited examples to another mode", () => {
    render(<ControlledForm enableTemplates />);
    const trigger = screen.getByRole("button", { name: "指令模板" });
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: /政策事实查询/ })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /查询|分析/ }).length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole("button", { name: /政策事实查询/ }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).not.toHaveValue("");

    fireEvent.click(screen.getByRole("tab", { name: "文献梳理" }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("");
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: /主题政策梳理/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /政策事实查询/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /主题政策梳理/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "我手动修改后的文献研究问题" },
    });
    fireEvent.click(screen.getByRole("tab", { name: "要素提取" }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("我手动修改后的文献研究问题");
    fireEvent.click(trigger);
    expect(screen.queryByText(/高级选择指定政策编号/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭指令模板" }));
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
  });

  it("closes the instruction dropdown with Escape or outside click, without submitting", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} enableTemplates />);
    const trigger = screen.getByRole("button", { name: "指令模板" });
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
    fireEvent.click(trigger);
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps the instruction chooser but never exposes advanced policy forms", () => {
    render(<ControlledForm enableTemplates />);
    fireEvent.click(screen.getByRole("tab", { name: "要素提取" }));
    fireEvent.click(screen.getByRole("button", { name: "指令模板" }));
    expect(screen.getByRole("region", { name: "指令模板列表" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /高级选择/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /政策编号/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /使用高级入口执行/ })).not.toBeInTheDocument();
  });

  it("does not show a template shortcut in the conversation composer by default", () => {
    render(<ControlledForm />);
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

  it("counts Unicode code points without truncating and blocks only over-limit sends", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    const textarea = screen.getByRole("textbox", { name: "研究需求" });
    const send = screen.getByRole("button", { name: "发送消息" });
    fireEvent.change(textarea, { target: { value: "😀".repeat(4000) } });
    expect(textarea).toHaveValue("😀".repeat(4000));
    expect(screen.getByText("4000/4000")).toBeInTheDocument();
    expect(send).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "😀".repeat(4001) } });
    expect(textarea).toHaveValue("😀".repeat(4001));
    expect(send).toBeDisabled();
    expect(screen.getByText(/输入过长/)).toBeInTheDocument();
  });

  it.each(["政策问答", "要素提取", "文献梳理", "政策比较", "多方案分析"])(
    "uses the same single natural-language composer for %s",
    (mode) => {
      const onSend = vi.fn();
      render(<ControlledForm onSend={onSend} />);
      fireEvent.click(screen.getByRole("tab", { name: mode }));
      const input = screen.getByRole("textbox", { name: "研究需求" });
      expect(screen.queryByRole("textbox", { name: /政策编号|研究对象编号|研究问题/ })).not.toBeInTheDocument();
      fireEvent.change(input, { target: { value: "根据研究要求自动查找政策并开展分析" } });
      fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
      expect(onSend).toHaveBeenCalledWith("根据研究要求自动查找政策并开展分析");
    },
  );

});
