import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { validationBatchApi } from "./validationBatch";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("validationBatchApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("uploads a workbook as multipart form data for preview", async () => {
    const file = new File(["workbook"], "questions.xlsx");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ upload_id: "upload-1", total: 1,
      columns: ["问题", "预期答案"], preview: [{ question: "问题？", expected: "答案" }] }));
    await expect(validationBatchApi.preview(file)).resolves.toMatchObject({ total: 1 });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toContain("/api/validation-batches/preview");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeInstanceOf(FormData);
    expect((init?.body as FormData).get("file")).toBe(file);
    expect(init?.headers).toBeUndefined();
  });

  it("encodes upload and job ids for start, status, and cancellation", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ job_id: "job-1", state: "queued", total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ job_id: "job/one", state: "running", total: 1, completed: 0, rows: [] }))
      .mockResolvedValueOnce(jsonResponse({ state: "cancelling" }));
    await validationBatchApi.start("upload/id");
    await validationBatchApi.status("job/one");
    await validationBatchApi.cancel("job/one");
    expect(vi.mocked(fetch).mock.calls.map(([url, init]) => [String(url), init?.method])).toEqual([
      [expect.stringContaining("/api/validation-batches/upload%2Fid/start"), "POST"],
      [expect.stringContaining("/api/validation-batches/jobs/job%2Fone"), undefined],
      [expect.stringContaining("/api/validation-batches/jobs/job%2Fone/cancel"), "POST"],
    ]);
  });

  it("downloads the result workbook and surfaces server error details", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("xlsx", { status: 200 }));
    const oldCreate = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const oldRevoke = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const create = vi.fn(() => "blob:validation");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const downloaded = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloaded({ href: this.href, filename: this.download });
    });
    try {
      await validationBatchApi.download("job/1");
      expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/jobs/job%2F1/export"));
      expect(downloaded).toHaveBeenCalledWith({ href: "blob:validation", filename: "guance-validation-results.xlsx" });
      vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: "任务尚未结束" }, 409));
      await expect(validationBatchApi.download("job-2")).rejects.toThrow("任务尚未结束");
    } finally {
      if (oldCreate) Object.defineProperty(URL, "createObjectURL", oldCreate);
      else Reflect.deleteProperty(URL, "createObjectURL");
      if (oldRevoke) Object.defineProperty(URL, "revokeObjectURL", oldRevoke);
      else Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  });
});
