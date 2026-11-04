import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { researchClient } from "./api/client";
import { createDemoResult } from "./data/demo";

vi.mock("./api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/client")>(),
  researchClient: { prepare: vi.fn(), submit: vi.fn(), resolveCompare: vi.fn(), resolveResearch: vi.fn() },
}));
vi.mock("./components/PolicyCatalog", () => ({
  PolicyCatalog: () => <div aria-label="政策目录测试页">目录</div>,
}));

const client = vi.mocked(researchClient);

async function ask(mode: string, question: string) {
  fireEvent.click(screen.getByRole("button", { name: `选择${mode}` }));
  fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
    target: { value: question },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
  });
}

describe("research-mode task constraints are delegated to the Research Agent", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.submit.mockImplementation(async (request) => createDemoResult(request, "success"));
  });
  afterEach(cleanup);

  it.each([
    ["政策比较", "compare", "比较衡水市与河北其他地市十三五产业结构目标"],
    ["要素提取", "extract", "提取北京市十四五规划的支持措施"],
    ["多方案分析", "compare_plans", "分析两个产业扶持方案的适用性"],
  ])("%s reaches the real run endpoint without an early document-selection gate", async (label, mode, question) => {
    render(<App />);
    await ask(label, question);
    expect(client.resolveResearch).not.toHaveBeenCalled();
    expect(client.prepare).not.toHaveBeenCalled();
    expect(client.submit).toHaveBeenCalledWith(expect.objectContaining({ task_type: mode, question }), expect.anything());
    expect(screen.queryByRole("button", { name: /高级选择/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /研究对象编号/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "指定政策对象" })).not.toBeInTheDocument();
  });

  it("keeps subsequent instructions as independent turns, without concatenating fabricated targets", async () => {
    render(<App />);
    await ask("多方案分析", "比较这两个方案");
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "请比较衡水市和保定市的十三五规划" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    });
    expect(client.submit).toHaveBeenCalledTimes(2);
    expect(client.submit).toHaveBeenNthCalledWith(1, expect.objectContaining({
      task_type: "compare_plans", question: "比较这两个方案",
    }), expect.anything());
    expect(client.submit).toHaveBeenNthCalledWith(2, expect.objectContaining({
      task_type: "compare_plans", question: "请比较衡水市和保定市的十三五规划",
    }), expect.anything());
    expect(client.resolveResearch).not.toHaveBeenCalled();
  });
});
