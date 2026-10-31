import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { researchClient } from "./api/client";
import { createDemoResult } from "./data/demo";
import type { ResearchRequest } from "./types";

vi.mock("./api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/client")>(),
  researchClient: { prepare: vi.fn(), submit: vi.fn(), resolveCompare: vi.fn(), resolveResearch: vi.fn() },
}));
vi.mock("./components/PolicyCatalog", () => ({
  PolicyCatalog: () => <div aria-label="政策目录测试页">目录</div>,
}));

const client = vi.mocked(researchClient);

function ready(request: ResearchRequest) {
  return {
    status: "ready" as const,
    request,
    message: "已从冻结目录自动定位研究对象",
    policies: [{ area: "衡水市", planid: "A", title: "政策A", fyp: "十三五" }],
  };
}

async function ask(mode: string, question: string) {
  fireEvent.click(screen.getByRole("button", { name: "更多功能" }));
  fireEvent.click(screen.getByRole("button", { name: /研究模式/ }));
  fireEvent.click(screen.getByRole("button", { name: mode }));
  fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
    target: { value: question },
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
  });
}

describe("natural-language research mode handoff", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.resolveResearch.mockImplementation(async (mode, question) => ready({
      task_type: mode, question, targets: mode === "extract" ? ["A"] : ["A", "B"],
    }));
    client.submit.mockImplementation(async (request) => createDemoResult(request, "success"));
  });
  afterEach(cleanup);

  it.each([
    ["政策比较", "compare", "比较衡水市与河北其他地市十三五产业结构目标"],
    ["要素提取", "extract", "提取北京市十四五规划的支持措施"],
    ["多方案分析", "compare_plans", "分析两个产业扶持方案的适用性"],
  ])("%s submits natural language without manual planids", async (label, mode, question) => {
    render(<App />);
    await ask(label, question);
    expect(client.resolveResearch).toHaveBeenCalledWith(mode, question);
    expect(client.submit).toHaveBeenCalledWith(
      expect.objectContaining({
        task_type: mode,
        question,
        targets: mode === "extract" ? ["A"] : ["A", "B"],
      }),
      expect.anything(),
    );
    expect(screen.queryByRole("button", { name: /高级选择/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /研究对象编号/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "指定政策对象" })).not.toBeInTheDocument();
  });

  it("only asks for missing context in words, then resumes the original research", async () => {
    client.resolveResearch.mockResolvedValueOnce({
      status: "needs_clarification", request: null,
      message: "请说明两个方案的名称，不需要政策编号。", policies: [],
    }).mockImplementationOnce(async (mode, question) => ready({
      task_type: mode, question, targets: ["A", "B"],
    }));
    render(<App />);
    await ask("多方案分析", "比较这两个方案");
    expect(screen.getByText("请说明两个方案的名称，不需要政策编号。")).toBeInTheDocument();
    expect(client.submit).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "衡水市和保定市的十三五规划" },
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    });
    expect(client.resolveResearch).toHaveBeenLastCalledWith(
      "compare_plans", "比较这两个方案；补充条件：衡水市和保定市的十三五规划",
    );
    expect(client.submit).toHaveBeenCalledTimes(1);
  });
});
