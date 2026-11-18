import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ValidationBatchPage } from "./ValidationBatchPage";
import { validationBatchApi } from "../api/validationBatch";

vi.mock("../api/validationBatch", () => ({
  validationBatchApi: { preview: vi.fn(), start: vi.fn(), status: vi.fn(), cancel: vi.fn(), download: vi.fn() },
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("ValidationBatchPage", () => {
  it("previews, starts, tracks a completed job, and makes the answer limit explicit", async () => {
    vi.mocked(validationBatchApi.preview).mockResolvedValue({ upload_id: "upload-1", total: 1,
      columns: ["问题", "预期答案"], preview: [{ question: "数字经济政策？", expected: "示例答案" }] });
    vi.mocked(validationBatchApi.start).mockResolvedValue({ job_id: "job-1", state: "queued", total: 1 });
    vi.mocked(validationBatchApi.status).mockResolvedValue({ job_id: "job-1", state: "finished", total: 1,
      completed: 1, rows: [{ index: 1, question: "数字经济政策？", status: "执行成功", evaluation: "未评估", duration: 1.2 }] });
    const file = new File(["workbook"], "questions.xlsx");
    render(<ValidationBatchPage />);

    fireEvent.change(screen.getByLabelText("选择验证集 Excel 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "解析并预览" }));
    expect(await screen.findByText("数字经济政策？")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始批量评测" }));
    expect(await screen.findByText("执行成功")).toBeInTheDocument();
    expect(screen.getByText(/不判断答案是否正确/)).toBeInTheDocument();
    expect(screen.getByText("未评估")).toBeInTheDocument();
    expect(sessionStorage.getItem("guance.validation.job")).toBe("job-1");
    fireEvent.click(screen.getByRole("button", { name: "导出模板 5 Excel" }));
    await waitFor(() => expect(validationBatchApi.download).toHaveBeenCalledWith("job-1"));
  });

  it("explains that a cancellation request can wait for the active question", async () => {
    sessionStorage.setItem("guance.validation.job", "job-running");
    vi.mocked(validationBatchApi.status).mockResolvedValue({ job_id: "job-running", state: "running",
      total: 3, completed: 1, rows: [{ index: 1, question: "当前问题", status: "执行成功", evaluation: "未评估", duration: 1 }] });
    vi.mocked(validationBatchApi.cancel).mockResolvedValue({ state: "cancelling" });
    render(<ValidationBatchPage />);
    expect(await screen.findByText("执行中")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "停止后续题目" }));
    expect(await screen.findByRole("status")).toHaveTextContent("正在处理的题目可能需要服务端响应或超时后");
    expect(validationBatchApi.cancel).toHaveBeenCalledWith("job-running");
  });

  it("shows API failures without hiding the upload form", async () => {
    vi.mocked(validationBatchApi.preview).mockRejectedValue(new Error("Excel 缺少必需列"));
    render(<ValidationBatchPage />);
    const file = new File(["bad"], "bad.xlsx");
    fireEvent.change(screen.getByLabelText("选择验证集 Excel 文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "解析并预览" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Excel 缺少必需列");
    expect(screen.getByRole("region", { name: "上传验证集" })).toBeInTheDocument();
  });

  it("keeps rendering and tracks the started job when session storage is blocked", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("storage blocked"); });
    vi.mocked(validationBatchApi.preview).mockResolvedValue({ upload_id: "upload-2", total: 1,
      columns: ["问题", "预期答案"], preview: [{ question: "可继续吗？", expected: "可以" }] });
    vi.mocked(validationBatchApi.start).mockResolvedValue({ job_id: "job-memory", state: "queued", total: 1 });
    vi.mocked(validationBatchApi.status).mockResolvedValue({ job_id: "job-memory", state: "running", total: 1,
      completed: 0, rows: [] });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage blocked"); });
    render(<ValidationBatchPage />);
    expect(getItem).toHaveBeenCalledWith("guance.validation.job");
    fireEvent.change(screen.getByLabelText("选择验证集 Excel 文件"), {
      target: { files: [new File(["workbook"], "questions.xlsx")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "解析并预览" }));
    fireEvent.click(await screen.findByRole("button", { name: "开始批量评测" }));
    expect(await screen.findByText("执行中")).toBeInTheDocument();
    expect(validationBatchApi.status).toHaveBeenCalledWith("job-memory");
  });

  it("clears an expired saved job and restores the upload flow on 404", async () => {
    sessionStorage.setItem("guance.validation.job", "job-expired");
    vi.mocked(validationBatchApi.status).mockRejectedValue(Object.assign(new Error("任务不存在或已过期"), { status: 404 }));
    render(<ValidationBatchPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("上次批量任务已过期或不存在");
    expect(screen.getByLabelText("选择验证集 Excel 文件")).toBeEnabled();
    expect(screen.getByRole("button", { name: "重新上传" })).toBeEnabled();
    expect(sessionStorage.getItem("guance.validation.job")).toBeNull();
  });
});
