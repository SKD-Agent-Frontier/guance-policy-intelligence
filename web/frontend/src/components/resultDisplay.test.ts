import { describe, expect, it } from "vitest";
import { containsInternalStatus, groupClaimsByDocument, prepareAnswerBody } from "./resultDisplay";
import type { Claim, EvidenceRef } from "../types";

const ref = (planid: string, chunkId: string, sectionPath = "第一章"): EvidenceRef => ({
  planid,
  chunk_id: chunkId,
  parent_id: null,
  title: `${planid} 政策`,
  area: "甲省",
  fyp: "十四五",
  admin_level: null,
  plantype_label: null,
  section_idx: null,
  section_path: sectionPath,
  char_start: 0,
  char_end: 10,
  chunk_char_start: 0,
  chunk_char_end: 10,
  truncated: false,
  source: "kb_read",
  observed_ranges: [[0, 10]],
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
});

const claim = (text: string, chunkIds: string[], quotes: string[]): Claim => ({
  text, chunk_ids: chunkIds, quotes, dimension: null, anchors: [],
});

describe("groupClaimsByDocument", () => {
  it("merges claims from the same planid into one group with a single primary entry", () => {
    const groups = groupClaimsByDocument(
      [claim("甲事实一", ["a-1"], ["甲引句一"]), claim("甲事实二", ["a-2"], ["甲引句二"]), claim("乙事实", ["b-1"], ["乙引句"])],
      [ref("P-A", "a-1", "第一章"), ref("P-A", "a-2", "第二章"), ref("P-B", "b-1")],
    );

    expect(groups).toHaveLength(2);
    const groupA = groups.find((group) => group.planid === "P-A");
    expect(groupA?.claims).toHaveLength(2);
    expect(groupA?.primaryChunkId).toBe("a-1");
    expect(groupA?.title).toBe("P-A 政策");
    // every claim keeps its own quote and chunk locators
    expect(groupA?.claims[1].quotes).toEqual(["甲引句二"]);
    expect(groupA?.claims[1].chunks.map((chunk) => chunk.chunkId)).toEqual(["a-2"]);
    expect(groups.find((group) => group.planid === "P-B")?.claims).toHaveLength(1);
  });

  it("keeps claims without a resolvable source in a single unmatched group", () => {
    const groups = groupClaimsByDocument([claim("无来源事实", ["missing"], [])], []);
    expect(groups).toHaveLength(1);
    expect(groups[0].planid).toBeNull();
    expect(groups[0].primaryChunkId).toBeNull();
    expect(groups[0].claims[0].chunks[0].reference).toBeNull();
  });
});

describe("prepareAnswerBody", () => {
  it("removes engineering prompts, duplicated fact bullets and the completion appendix", () => {
    const body = prepareAnswerBody([
      "根据本轮已读政策原文，可提取以下具有逐字引文的事实。是否足以回应全部研究要求仍需单独评估：",
      "- 政策涉及基础设施建设",
      "",
      "研究完成情况",
      "",
      "完成状态：部分完成",
      "完成度缺口：claims_removed_or_answer_rebuilt",
    ].join("\n"), [claim("政策涉及基础设施建设", ["a"], ["建设基础设施"])]);

    expect(body).toBe("");
  });

  it("keeps real report headings and paragraphs while dropping internal sections", () => {
    const body = prepareAnswerBody([
      "## 研究问题",
      "",
      "本轮研究关注产业园区支持政策。",
      "",
      "## 研究局限",
      "",
      "基于本轮已审查的证据；不代表已穷尽全部政策文献。",
      "",
      "## 研究完成情况",
      "",
      "完成状态：部分完成",
    ].join("\n"), []);

    expect(body).toContain("## 研究问题");
    expect(body).toContain("本轮研究关注产业园区支持政策。");
    expect(body).toContain("## 研究局限");
    expect(body).toContain("不代表已穷尽全部政策文献");
    expect(body).not.toContain("研究完成情况");
    expect(body).not.toContain("完成状态");
  });

  it("strips a hedging parenthetical from report headings without removing the heading", () => {
    const body = prepareAnswerBody([
      "## 已核验政策事实（非完整研究结论）",
      "",
      "一项补充事实。",
      "",
      "## 研究发现与分析",
      "",
      "研究发现正文。",
    ].join("\n"), []);
    expect(body).toContain("已核验政策事实");
    expect(body).not.toContain("非完整研究结论");
    expect(body).toContain("研究发现与分析");
    expect(body).toContain("一项补充事实。");
    expect(body).toContain("研究发现正文。");
  });

  it("keeps the backend's short user-facing refusal instead of dropping it as a disclaimer", () => {
    const body = prepareAnswerBody(
      "本轮未取得可核验的政策事实，不能据此作出判断；未找到某项内容不代表政策中不存在该项内容。",
      [],
    );
    expect(body).toContain("本轮未取得可核验的政策事实");
    expect(body).toContain("不代表政策中不存在该项内容");
  });

  it("keeps report body when every report section duplicates claim text", () => {
    const body = prepareAnswerBody([
      "## 农业领域",
      "",
      "推进农业科技进步。",
      "",
      "## 工业领域",
      "",
      "强力启动高新技术产业。",
    ].join("\n"), [
      claim("推进农业科技进步。", ["a"], ["推进农业科技进步"]),
      claim("强力启动高新技术产业。", ["b"], ["强力启动高新技术产业"]),
    ]);

    expect(body).toContain("## 农业领域");
    expect(body).toContain("推进农业科技进步。");
    expect(body).toContain("## 工业领域");
  });

  it("treats raw delivery codes as internal status", () => {
    expect(containsInternalStatus("claims_removed_or_answer_rebuilt")).toBe(true);
    expect(containsInternalStatus("research_quality")).toBe(true);
    expect(containsInternalStatus("status: partial")).toBe(true);
    expect(containsInternalStatus("政策提出加强基础设施建设。")).toBe(false);
  });
});
