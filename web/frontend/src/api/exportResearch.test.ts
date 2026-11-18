import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RunResult } from "../types";

const runtime = vi.hoisted(() => ({ isRealMode: true }));
vi.mock("./config", () => ({
  apiBaseUrl: "https://api.example.test",
  get isRealMode() { return runtime.isRealMode; },
}));

import { exportResearch } from "./exportResearch";

const result = {
  run_id: "run-1", session_id: "session-1", task_type: "qa",
  status: "completed", evidence_status: "insufficient",
  answer: "结论", claims: [], evidence_refs: [], dropped_chunk_ids: [],
  error: null, warnings: ["有内容尚未核验"], meta: { model_log: "must not export" },
  structured: { verification: { policy_facts: "unverified" } }, route: null,
} as unknown as RunResult;

describe("exportResearch", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    runtime.isRealMode = true;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it.each(["docx", "xlsx"] as const)("posts the safe result contract and downloads %s", async (format) => {
    vi.mocked(fetch).mockResolvedValue(new Response("file-content", { status: 200 }));
    const oldCreate = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
    const oldRevoke = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
    const createObjectURL = vi.fn(() => "blob:test-export");
    const revokeObjectURL = vi.fn();
    const downloaded = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloaded({ href: this.href, filename: this.download });
    });
    try {
      await exportResearch(result, "研究问题", format);
      const [url, init] = vi.mocked(fetch).mock.calls[0];
      expect(url).toBe(`https://api.example.test/api/exports/${format}`);
      expect(init?.method).toBe("POST");
      expect(init?.headers).toEqual({ "Content-Type": "application/json" });
      expect(JSON.parse(String(init?.body))).toEqual({
        question: "研究问题",
        result: {
          answer: "结论", claims: [], evidence_refs: [], structured: result.structured,
          task_type: "qa", status: "completed", evidence_status: "insufficient",
        },
      });
      expect(JSON.stringify(JSON.parse(String(init?.body)))).not.toContain("must not export");
      expect(click).toHaveBeenCalledOnce();
      expect(downloaded).toHaveBeenCalledWith({
        href: "blob:test-export",
        filename: expect.stringMatching(new RegExp(`^guance-research-\\d{4}-\\d{2}-\\d{2}\\.${format}$`)),
      });
      expect(createObjectURL).toHaveBeenCalledOnce();
      await new Promise(resolve => window.setTimeout(resolve, 0));
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:test-export");
    } finally {
      if (oldCreate) Object.defineProperty(URL, "createObjectURL", oldCreate);
      else Reflect.deleteProperty(URL, "createObjectURL");
      if (oldRevoke) Object.defineProperty(URL, "revokeObjectURL", oldRevoke);
      else Reflect.deleteProperty(URL, "revokeObjectURL");
    }
  });

  it("rejects incomplete results before sending a request", async () => {
    await expect(exportResearch({ ...result, status: "failed" } as RunResult, "问题", "docx"))
      .rejects.toThrow("研究尚未完成");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects demo mode before sending a request", async () => {
    runtime.isRealMode = false;
    await expect(exportResearch(result, "问题", "xlsx")).rejects.toThrow("演示模式");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("shows the server detail on an export failure", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: "导出内容过大" }), {
      status: 413, headers: { "Content-Type": "application/json" },
    }));
    await expect(exportResearch(result, "问题", "docx")).rejects.toThrow("导出内容过大");
  });
});
