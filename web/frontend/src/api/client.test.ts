import { afterEach, describe, expect, it, vi } from "vitest";
import { researchClient } from "./client";
import type { ResearchRequest } from "../types";

const request = (overrides: Partial<ResearchRequest> = {}): ResearchRequest => ({
  question: "示例：演示不同人才支持方式",
  task_type: "compare",
  targets: ["DEMO-A", "DEMO-B"],
  ...overrides,
});

async function submitAndAdvance(input: ResearchRequest, scenario: "success" | "insufficient" | "empty" | "failed" | "limit_reached") {
  const pending = researchClient.submit(input, scenario);
  await vi.advanceTimersByTimeAsync(900);
  return pending;
}

describe("researchClient demo responses", () => {
  afterEach(() => vi.useRealTimers());

  it("waits before returning a clearly marked synthetic response", async () => {
    vi.useFakeTimers();
    const pending = researchClient.submit(request());

    await vi.advanceTimersByTimeAsync(899);
    let resolved = false;
    void pending.then(() => { resolved = true; });
    await Promise.resolve();
    expect(resolved).toBe(false);

    await vi.advanceTimersByTimeAsync(1);
    const result = await pending;
    expect(result.answer).toContain("示例结果");
    expect(result.warnings.join(" ")).toContain("合成内容");
    expect(result.meta).toMatchObject({ is_mock: true, demo_question: "示例：演示不同人才支持方式" });
  });

  it("keeps compare targets, cells, claims, quotes, and evidence aligned for any supported column count", async () => {
    vi.useFakeTimers();
    const targets = ["DEMO-A", "DEMO-B", "DEMO-C", "DEMO-D", "DEMO-E", "DEMO-F"];
    const result = await submitAndAdvance(request({ targets }), "success");
    const structured = result.structured;

    expect(result.route?.targets).toEqual(targets);
    expect(structured).not.toBeNull();
    if (!structured || !("matrix" in structured)) throw new Error("Expected a comparison result");
    expect(structured.objects.map((object) => object.planid)).toEqual(targets);
    expect(structured.matrix[0].cells.map((cell) => cell.planid)).toEqual(targets);
    expect(structured.matrix).toHaveLength(3);
    for (const row of structured.matrix) {
      expect(row.cells).toHaveLength(targets.length);
      for (const cell of row.cells) {
        expect(cell.status).toBe("found");
        const claim = result.claims.find((item) => item.chunk_ids.some((chunkId) => cell.chunk_ids?.includes(chunkId)));
        expect(cell.quotes).toEqual(claim?.quotes);
        expect(cell.text).toBe(claim?.quotes[0]);
      }
    }
    expect([...new Set(result.evidence_refs.map((ref) => ref.planid))]).toEqual(targets);
  });

  it("uses the extract contract and keeps QA structured output null", async () => {
    vi.useFakeTimers();
    const extract = await submitAndAdvance(request({ task_type: "extract", targets: ["DEMO-A"], dimensions: ["适用对象"] }), "success");
    expect(extract.task_type).toBe("extract");
    expect(extract.structured).toMatchObject({ dimensions: ["适用对象"], documents: [{ planid: "DEMO-A" }], elements: extract.claims, missing: [] });
    expect(extract.claims).toHaveLength(1);

    const qa = await submitAndAdvance(request({ task_type: "qa" }), "success");
    expect(qa.task_type).toBe("qa");
    expect(qa.structured).toBeNull();
  });

  it("creates an element for every requested extract dimension", async () => {
    vi.useFakeTimers();
    const dimensions = ["适用对象", "政策工具", "量化目标"];
    const result = await submitAndAdvance(request({ task_type: "extract", targets: ["DEMO-A"], dimensions }), "success");
    expect(result.claims.map((claim) => claim.dimension)).toEqual(dimensions);
    expect(result.evidence_refs.map((ref) => ref.chunk_id)).toEqual(result.claims.flatMap((claim) => claim.chunk_ids));
  });

  it("does not leak claims or structured data between empty and failed runs", async () => {
    vi.useFakeTimers();
    const success = await submitAndAdvance(request({ task_type: "organize", targets: undefined }), "success");
    const empty = await submitAndAdvance(request({ task_type: "organize", targets: undefined }), "empty");
    const failed = await submitAndAdvance(request({ task_type: "organize", targets: undefined }), "failed");

    expect(success.claims.length).toBeGreaterThan(0);
    if (!success.structured || !("policies" in success.structured)) throw new Error("Expected an organize result");
    expect(success.structured.scope.targets).toBeNull();
    expect(success.structured.policies).toHaveLength(3);
    expect(success.structured.coverage.documents_summarized).toBe(3);
    expect(empty.claims).toEqual([]);
    expect(empty.structured).toEqual({});
    expect(failed.claims).toEqual([]);
    expect(failed.structured).toEqual({});
    expect(failed.error?.code).toBe("DEMO_FAILURE");
  });

  it("returns only a partial claim for insufficient evidence and an empty structure at the limit", async () => {
    vi.useFakeTimers();
    const insufficient = await submitAndAdvance(request({ task_type: "compare" }), "insufficient");
    const limited = await submitAndAdvance(request({ task_type: "compare" }), "limit_reached");
    const comparison = insufficient.structured;

    expect(insufficient.evidence_status).toBe("insufficient");
    expect(insufficient.claims).toHaveLength(3);
    if (!comparison || !("matrix" in comparison)) throw new Error("Expected a comparison result");
    expect(comparison.matrix).toHaveLength(3);
    for (const row of comparison.matrix) expect(row.cells.map((cell) => cell.status)).toEqual(["found", "insufficient"]);
    expect(limited.status).toBe("limit_reached");
    expect(limited.claims).toEqual([]);
    expect(limited.structured).toEqual({});
  });
});
