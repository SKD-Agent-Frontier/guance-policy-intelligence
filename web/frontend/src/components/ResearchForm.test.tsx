import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchForm } from "./ResearchForm";
import type { TaskType } from "../types";

function ControlledForm({
  onSend = vi.fn(),
}: {
  onSend?: (message: string) => void;
}) {
  const [mode, setMode] = useState<TaskType>("qa");
  const [value, setValue] = useState("");
  return <ResearchForm mode={mode} value={value} busy={false}
    onChange={setValue} onModeChange={setMode} onSend={onSend} />;
}

function openModes() {
  fireEvent.click(screen.getByRole("button", { name: "更多功能" }));
  fireEvent.click(screen.getByRole("button", { name: /研究模式/ }));
}

function openTemplates() {
  fireEvent.click(screen.getByRole("button", { name: "更多功能" }));
  fireEvent.click(screen.getByRole("button", { name: "指令模板" }));
}

describe("ResearchForm plus menu", () => {
  afterEach(cleanup);

  it("shows one compact plus entry and keeps all five mode constraints inside it", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    expect(screen.queryByRole("tablist", { name: "研究模式" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "更多功能" })).toHaveAttribute("aria-expanded", "false");
    openModes();
    expect(screen.getByRole("region", { name: "研究模式选择" })).toBeInTheDocument();
    for (const label of ["政策问答", "文献梳理", "要素提取", "政策比较", "多方案分析"])
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "政策比较" }));
    expect(screen.getByText("政策比较")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "研究模式选择" })).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "政策比较" }));
    fireEvent.click(screen.getByRole("button", { name: /发展目标比较/ }));
    const input = screen.getByRole("textbox", { name: "研究需求" });
    expect(input).toHaveValue("比较浙江和江苏十四五数字经济发展目标的异同");
    expect(input).toHaveFocus();
    expect(screen.queryByRole("region", { name: "指令模板列表" })).not.toBeInTheDocument();
    openModes();
    expect(screen.getByRole("button", { name: "政策问答" })).toHaveAttribute("aria-pressed", "true");
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "政策比较" }));
    expect(input).toHaveValue("");
    fireEvent.change(input, { target: { value: "修改后比较政策支持措施" } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(onSend).toHaveBeenCalledWith("修改后比较政策支持措施");
  });

  it("preserves edited user text when changing mode", () => {
    render(<ControlledForm />);
    openTemplates();
    fireEvent.click(screen.getByRole("button", { name: /政策事实查询/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "我手工修改的研究要求" },
    });
    openModes();
    fireEvent.click(screen.getByRole("button", { name: "要素提取" }));
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
    openModes();
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
    expect(screen.getByText("4000/4000")).toBeInTheDocument();
    expect(send).toBeEnabled();
    fireEvent.change(textarea, { target: { value: "😀".repeat(4001) } });
    expect(send).toBeDisabled();
  });
});
