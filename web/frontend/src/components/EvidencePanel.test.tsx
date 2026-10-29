import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidencePanel } from "./EvidencePanel";
import type { EvidenceRef, RunResult } from "../types";

const reference: EvidenceRef = {
  planid: "DEMO-2",
  chunk_id: "chunk-current",
  parent_id: null,
  title: "当前证据文献",
  area: null,
  fyp: null,
  admin_level: null,
  plantype_label: null,
  section_idx: null,
  section_path: "",
  char_start: 0,
  char_end: 30,
  chunk_char_start: null,
  chunk_char_end: null,
  truncated: false,
  source: "kb_read",
  observed_ranges: [[0, 30]],
  complete: true,
  out_of_section: false,
  document_id: null,
  document_version_id: null,
  generation_id: null,
  source_hash: null,
  section_id: null,
  section_char_start: null,
  section_char_end: null,
  offset_basis: null,
  offset_unit: null,
  body_origin: null,
  contract_version: null,
  quality_warnings: [],
};

const result: RunResult = {
  run_id: "run-current",
  session_id: "session-current",
  status: "completed",
  evidence_status: "verified",
  answer: "",
  claims: [
    { text: "当前关联结论", chunk_ids: ["chunk-current"], quotes: ["当前任务的原文引句。", "同一关联结论的第二句引文。"], dimension: "支持对象", anchors: [] },
    { text: "其他任务残留结论", chunk_ids: ["chunk-old"], quotes: ["不应显示的旧引句。"], dimension: null, anchors: [] },
  ],
  evidence_refs: [reference],
  dropped_chunk_ids: [],
  error: null,
  meta: {},
  warnings: [],
  task_type: "qa",
  structured: null,
  route: null,
};

describe("EvidencePanel", () => {
  it("shows missing metadata and quotes only from claims linked to the selected chunk", () => {
    const html = renderToStaticMarkup(<EvidencePanel result={result} selectedChunkId="chunk-current" onClose={() => {}} />);

    expect(html).toContain("当前证据文献");
    expect(html).toContain("DEMO-2");
    expect(html).toContain("章节路径");
    expect(html.match(/未提供/g)?.length).toBeGreaterThanOrEqual(3);
    expect(html).toContain("当前任务的原文引句。");
    expect(html).toContain("同一关联结论的第二句引文。");
    expect(html).toContain("来源含本片段");
    expect(html).not.toContain("不应显示的旧引句。");
    expect(html).toContain("不保证引句在语义上足以支持结论");
  });
});
