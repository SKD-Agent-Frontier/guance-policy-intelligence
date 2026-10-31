import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { researchClient } from "./api/client";
import type { PreparationResponse, ResearchPreparationRequest } from "./types";

vi.mock("./api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("./api/client")>(),
  researchClient: { prepare: vi.fn(), submit: vi.fn(), resolveCompare: vi.fn() },
}));

vi.mock("./components/PolicyCatalog", () => ({
  PolicyCatalog: () => <div aria-label="政策目录测试页">目录</div>,
}));

function needsClarification(request: ResearchPreparationRequest): PreparationResponse {
  return {
    status: "needs_clarification",
    mode: request.mode,
    message: "请选择研究方面",
    options: [{ id: "aspect", question: "请选择研究方面", choices: ["发展目标"] }],
    state: {
      state_id: `state-${request.message}`,
      revision: 0,
      expires_at: "2027-01-01T00:00:00Z",
      mode: request.mode,
      original_request: request.message,
      topic: "研究主题",
      time_scope: null,
      area: null,
      dimensions: [],
      confirmed_conditions: [],
      pending_conditions: [],
      completed_questions: [],
      clarification_count: 0,
      condition_statuses: [],
    },
    request: null,
    warnings: [],
  };
}

const client = vi.mocked(researchClient);

async function submitHome(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), { target: { value: text } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "发送消息" })); });
}

describe("independent home composer and history conversations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.prepare.mockImplementation(async (request) => needsClarification(request));
  });
  afterEach(cleanup);

  it("resets the home composer and starts a fresh conversation for each home submission", async () => {
    render(<App />);

    await submitHome("第一项数字经济研究");
    expect(screen.getByRole("main", { name: "研究工作区" })).toBeInTheDocument();
    expect(client.prepare).toHaveBeenNthCalledWith(1, {
      mode: "qa", message: "第一项数字经济研究",
    });

    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("");
    expect(screen.getByRole("tab", { name: "政策问答" })).toHaveAttribute("aria-selected", "true");

    await submitHome("第二项产业扶持研究");
    expect(client.prepare).toHaveBeenNthCalledWith(2, {
      mode: "qa", message: "第二项产业扶持研究",
    });
    expect(screen.getByRole("main", { name: "研究工作区" })).toHaveTextContent("第二项产业扶持研究");
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(screen.getByTitle("第一项数字经济研究")).toBeInTheDocument();
    expect(screen.getByTitle("第二项产业扶持研究")).toBeInTheDocument();
    expect(screen.queryByTitle("新对话")).not.toBeInTheDocument();

    // Reopening history resumes the old preparation state, not a new conversation.
    fireEvent.click(screen.getByTitle("第一项数字经济研究"));
    expect(screen.getByRole("main", { name: "研究工作区" })).toHaveTextContent("第一项数字经济研究");
    fireEvent.change(screen.getByRole("textbox", { name: "研究需求" }), {
      target: { value: "继续追问实施路径" },
    });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "发送消息" })); });
    expect(client.prepare).toHaveBeenNthCalledWith(3, {
      mode: "qa",
      message: "继续追问实施路径",
      state: { state_id: "state-第一项数字经济研究", revision: 0 },
    });
    expect(screen.getByTitle("第二项产业扶持研究")).toBeInTheDocument();
  });

  it("restores default QA home mode independently of an earlier comparison session", async () => {
    client.resolveCompare.mockResolvedValue({
      status: "needs_clarification",
      message: "当前发布库需要更明确的政策对象",
      targets: [],
      policies: [],
      dimensions: [],
    });
    render(<App />);
    fireEvent.click(screen.getByRole("tab", { name: "政策比较" }));
    await submitHome("比较浙江和江苏十四五的数字经济政策");
    expect(client.resolveCompare).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("main", { name: "研究工作区" })).toHaveTextContent("比较浙江和江苏十四五的数字经济政策");

    fireEvent.click(screen.getByRole("button", { name: "管策智研首页" }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("");
    expect(screen.getByRole("tab", { name: "政策问答" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("textbox", { name: "研究问题" })).not.toBeInTheDocument();

    await submitHome("有哪些产业支持政策");
    expect(client.prepare).toHaveBeenCalledWith({
      mode: "qa", message: "有哪些产业支持政策",
    });
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    fireEvent.click(screen.getByTitle("比较浙江和江苏十四五的数字经济政策"));
    expect(screen.getByRole("main", { name: "研究工作区" })).toHaveTextContent("比较浙江和江苏十四五的数字经济政策");
    expect(screen.getByRole("textbox", { name: "研究问题" })).toHaveValue("比较浙江和江苏十四五的数字经济政策");
  });

  it("can start another home conversation while the original session is still preparing", async () => {
    client.prepare
      .mockImplementationOnce(() => new Promise<PreparationResponse>(() => {}))
      .mockImplementationOnce(async (request) => needsClarification(request));
    render(<App />);

    await submitHome("还在准备的研究");
    expect(client.prepare).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "返回首页" }));
    expect(screen.getByRole("textbox", { name: "研究需求" })).toHaveValue("");

    await submitHome("新的独立研究");
    expect(client.prepare).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("main", { name: "研究工作区" })).toHaveTextContent("新的独立研究");
    fireEvent.click(screen.getByRole("button", { name: "展开侧栏" }));
    expect(screen.getByTitle("还在准备的研究")).toHaveTextContent("处理中");
    expect(screen.getByTitle("新的独立研究")).toBeInTheDocument();
  });
});
