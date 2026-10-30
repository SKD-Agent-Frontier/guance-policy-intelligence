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
  return (
    <ResearchForm
      mode={mode}
      value={value}
      busy={false}
      onChange={setValue}
      onModeChange={setMode}
      onSend={() => onSend(value)}
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

    fireEvent.click(screen.getByRole("tab", { name: "文献梳理" }));
    expect(onSend).not.toHaveBeenCalled();
  });

  it("grows for long text and respects Enter, Shift+Enter, and IME composition", () => {
    const onSend = vi.fn();
    render(<ControlledForm onSend={onSend} />);
    const textarea = screen.getByRole("textbox", { name: "研究需求" });
    Object.defineProperty(textarea, "scrollHeight", { configurable: true, value: 560 });

    fireEvent.change(textarea, { target: { value: "长文本".repeat(1200) } });
    expect(textarea).toHaveStyle({ height: "320px", overflowY: "auto" });

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
});
