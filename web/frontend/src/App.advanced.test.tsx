import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { researchClient } from "./api/client";

vi.mock("./api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/client")>(),
  researchClient: { prepare: vi.fn(), submit: vi.fn() },
}));

vi.mock("./components/PolicyCatalog", () => ({
  PolicyCatalog: () => <div aria-label="政策目录测试页">目录</div>,
}));

describe("advanced conversation handoff", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(cleanup);

  it.each([
    ["政策比较", "比较下北京市2015和2016年的政府工作报告"],
    ["要素提取", "提取北京市十四五规划中关于人工智能产业发展的支持措施"],
    ["多方案分析", "分析这几份产业扶持政策的优势和局限"],
  ])("opens %s from home with one normal guide and no network task", (mode, question) => {
    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: mode }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), { target: { value: question } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));

    expect(screen.getByRole("main", { name: "研究工作区" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue(question);
    expect(screen.getByRole("button", { name: "收起高级选择" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("button", { name: "指定政策对象" })).toHaveLength(1);
    expect(document.querySelector(".message-error")).toBeNull();
    expect(screen.queryByText(/UNSUPPORTED_MODE/)).not.toBeInTheDocument();
    expect(researchClient.prepare).not.toHaveBeenCalled();
    expect(researchClient.submit).not.toHaveBeenCalled();
  });

  it("preserves question, IDs and dimensions through catalog, home and conversation switching", () => {
    const question = "比较两份政策的企业支持措施";
    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: "政策比较" }));
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), { target: { value: question } });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    fireEvent.change(screen.getByLabelText("研究对象编号 1"), { target: { value: "POLICY-A" } });
    fireEvent.click(screen.getByRole("button", { name: "资金标准" }));
    fireEvent.change(screen.getByRole("textbox", { name: "自定义维度（选填）" }), { target: { value: "企业规模" } });
    fireEvent.click(screen.getByRole("button", { name: "前往文献目录查找编号" }));
    expect(screen.getByLabelText("政策目录测试页")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "研究" }));
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue(question);
    expect(screen.getByLabelText("研究对象编号 1")).toHaveValue("POLICY-A");
    expect(screen.getByRole("button", { name: "资金标准" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("textbox", { name: "自定义维度（选填）" })).toHaveValue("企业规模");

    fireEvent.click(screen.getByRole("button", { name: "管策智研首页" }));
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue(question);
    fireEvent.click(screen.getByRole("button", { name: "研究" }));
    fireEvent.click(screen.getByRole("button", { name: "打开侧栏" }));
    fireEvent.click(screen.getByRole("button", { name: "新建研究" }));
    fireEvent.click(screen.getByRole("tab", { name: "政策比较" }));
    fireEvent.click(screen.getByRole("button", { name: "高级选择 · 指定政策编号" }));
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue("");
    fireEvent.click(screen.getByTitle(question));
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue(question);
    expect(screen.getByLabelText("研究对象编号 1")).toHaveValue("POLICY-A");
    fireEvent.click(screen.getByRole("button", { name: "收起高级选择" }));
    fireEvent.click(screen.getByRole("button", { name: "指定政策对象" }));
    expect(screen.getByLabelText("研究对象编号 2")).toHaveFocus();
    expect(researchClient.prepare).not.toHaveBeenCalled();
    expect(researchClient.submit).not.toHaveBeenCalled();
  });
});
