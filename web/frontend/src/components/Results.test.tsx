import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Results } from "./Results";
import type { Claim, ComparisonResult, DocumentIdentityFallback, EvidenceRef, RunResult } from "../types";

const makeEvidenceRef = (planid: string, chunkId: string, overrides: Partial<EvidenceRef> = {}): EvidenceRef => ({
  planid,
  chunk_id: chunkId,
  parent_id: null,
  title: `${planid} 政策`,
  area: null,
  fyp: null,
  admin_level: null,
  plantype_label: null,
  section_idx: null,
  section_path: "",
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
  ...overrides,
});

const makeResult = (overrides: Partial<RunResult> = {}): RunResult => ({
  run_id: "run-current",
  session_id: "session-current",
  status: "completed",
  evidence_status: "verified",
  answer: "当前结果说明。",
  claims: [],
  evidence_refs: [],
  dropped_chunk_ids: [],
  error: null,
  meta: {},
  warnings: [],
  task_type: "compare",
  structured: null,
  route: null,
  ...overrides,
});

const noEvidence = () => {};

describe("Results", () => {
  it("adds superscripts only to explicitly linked report blocks and keeps unlinked text uncited", () => {
    const claims: Claim[] = [
      { text: "甲政策事实", chunk_ids: ["a"], quotes: ["甲原文"], dimension: null, anchors: [] },
      { text: "乙政策事实", chunk_ids: ["b"], quotes: ["乙原文"], dimension: null, anchors: [] },
    ];
    const html = renderToStaticMarkup(<Results result={makeResult({ task_type: "qa", answer: "甲乙政策存在差异。\n\n其他说明。", claims,
      evidence_refs: [makeEvidenceRef("A", "a"), makeEvidenceRef("B", "b")],
      structured: { schema_version: "qa-report/1", blocks: [{ text: "甲乙政策存在差异。", supporting_claim_indexes: [0, 1], supporting_chunk_ids: ["a", "b", "unknown"] }] } as never,
    })} onEvidence={noEvidence} />);
    expect(html).toMatch(/甲乙政策存在差异。<sup[^>]*>.*?\[1\].*?<sup[^>]*>.*?\[2\]/);
    expect(html).toContain("其他说明。</p>");
    expect(html).not.toContain('data-chunk-id="unknown"');
    expect(html.indexOf("引用文件")).toBeGreaterThan(html.indexOf("其他说明。"));
  });

  it("does not invent a citation when a claim references a missing source", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({ task_type: "qa", answer: "政策事实。", claims: [{ text: "政策事实。", chunk_ids: ["missing"], quotes: [], dimension: null, anchors: [] }], evidence_refs: [] })} onEvidence={noEvidence} />);
    expect(html).toContain("政策事实。");
    expect(html).not.toContain("qa-inline-citation");
    expect(html).not.toContain("引用文件");
  });
  it.each([null, {}] as const)("does not render a previous matrix when structured is %s", (structured) => {
    const html = renderToStaticMarkup(<Results result={makeResult({ structured })} onEvidence={noEvidence} />);
    expect(html).toContain("当前结果说明");
    expect(html).not.toContain("<table");
    expect(html).not.toContain("旧政策矩阵");
  });

  it("shows documents-only fallback as identity metadata without policy facts", () => {
    const identityFallback: DocumentIdentityFallback = {
      documents: [{ title: "演示文献身份", planid: "DEMO-1", area: null, fyp: null }],
      verification: { policy_facts: "unverified" },
    };
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "organize",
      answer: "",
      structured: identityFallback,
    })} onEvidence={noEvidence} />);

    expect(html).toContain("演示文献身份");
    expect(html).toContain("DEMO-1");
    expect(html).toContain("当前仅有来源身份信息");
    expect(html).not.toContain("支持金额");
  });

  it("shows scoped QA analysis as interpretation with one main source entry per document", () => {
    const facts: Claim[] = [
      { text: "政策甲提到产业园区", chunk_ids: ["A"], quotes: ["产业园区"], dimension: null, anchors: [] },
      { text: "政策乙提到企业改造", chunk_ids: ["B"], quotes: ["企业改造"], dimension: null, anchors: [] },
    ];
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: "事实列举",
      claims: facts,
      evidence_refs: [
        makeEvidenceRef("PA", "A", { title: "政策甲规划", area: "甲省", fyp: "十四五", section_path: "第一章" }),
        makeEvidenceRef("PB", "B", { title: "政策乙规划", area: "乙省", fyp: "十四五", section_path: "第二章" }),
      ],
      structured: {
        schema_version: "qa-analysis/1",
        analyses: [{
          kind: "comparison",
          text: "两项政策使用了不同的政策工具。",
          scope_note: "仅限本轮已读条款",
          supporting_claim_indexes: [0, 1],
          supporting_chunk_ids: ["A", "B"],
          verification: "evidence_linked_only",
        }],
        verification: {
          policy_facts: "existing_quote_and_number_checks",
          analysis: "dependency_links_checked_semantics_not_verified",
        },
      },
    })} onEvidence={noEvidence} />);
    expect(html).toContain("研究分析");
    expect(html).toContain("两项政策使用了不同的政策工具");
    expect(html).toContain("不是政策原文");
    expect(html).toContain("政策甲提到产业园区");
    expect(html).toContain("政策乙提到企业改造");
    expect(html.match(/class="qa-reference-number"/g)).toHaveLength(2);
    expect(html).toContain('data-chunk-id="A"');
    expect(html).toContain('data-chunk-id="B"');
  });

  it("renders failed QA status without an empty conclusion", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      status: "failed",
      error: { code: "OUTPUT_PARSE", message: "解析失败" },
      evidence_status: "none",
    })} onEvidence={noEvidence} />);
    expect(html).toContain("本轮研究未完成");
    expect(html).not.toContain("<h1>");
  });

  it("renders partial research with a single 研究结果 title and no internal status or disclaimers", () => {
    const claim: Claim = {
      text: "政策涉及基础设施建设",
      chunk_ids: ["chunk-a"],
      quotes: ["建设基础设施"],
      dimension: null,
      anchors: [],
    };
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      evidence_status: "insufficient",
      answer: [
        "根据本轮已读政策原文，可提取以下具有逐字引文的事实。是否足以回应全部研究要求仍需单独评估：",
        "- 政策涉及基础设施建设",
        "",
        "研究完成情况",
        "",
        "完成状态：部分完成",
        "完成度缺口：claims_removed_or_answer_rebuilt",
      ].join("\n"),
      claims: [claim],
      evidence_refs: [makeEvidenceRef("P-1", "chunk-a", {
        title: "政策甲", area: "甲省", fyp: "十四五", section_path: "第一章",
      })],
      meta: {
        research_quality: { status: "partial", gaps: ["claims_removed_or_answer_rebuilt"] },
        research_completion: { status: "partial", gaps: ["claims_removed_or_answer_rebuilt"] },
        research_delivery: { format: "evidence_linked_report", status: "partial", audited_analysis: false },
      },
    })} onEvidence={noEvidence} />);

    expect(html).not.toContain("<h1>研究结果</h1>");
    expect(html).not.toContain("研究结果（部分）");
    expect(html).not.toContain("研究结论");
    expect(html).not.toContain("仅展示已经核验");
    expect(html).not.toContain("尚未完成的研究分析不得视为结论");
    expect(html).not.toContain("根据本轮已读政策原文");
    expect(html).not.toContain("研究完成情况");
    expect(html).not.toContain("claims_removed_or_answer_rebuilt");
    expect(html).not.toContain("research_quality");
    expect(html).not.toContain("research_completion");
    expect(html).not.toContain("partial");
    // The fact is rendered once by its source group, not duplicated from the answer body.
    expect(html.match(/政策涉及基础设施建设/g)).toHaveLength(1);
    expect(html).toContain("引用文件");
  });

  it("renders source-excerpt recovery without partial labels or extra disclaimers", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      evidence_status: "insufficient",
      answer: "以下仅为核验原文节选",
      meta: {
        execution_recovery: { source: "kb_read" },
        research_delivery: { status: "partial", audited_analysis: false },
      },
    })} onEvidence={noEvidence} />);
    expect(html).not.toContain("<h1>研究结果</h1>");
    expect(html).not.toContain("研究结果（部分）");
    expect(html).not.toContain("本轮仅取得可核验的政策原文节选");
    expect(html).not.toContain("尚未形成完整研究结论");
  });

  it("keeps plain QA layout without analysis", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: "原有政策事实",
      claims: [],
      structured: null,
    })} onEvidence={noEvidence} />);
    expect(html).not.toContain("<h1>研究结果</h1>");
    expect(html).not.toContain("研究分析");
  });

  it("renders one primary original entry per planid and keeps per-claim evidence expandable", () => {
    const claims: Claim[] = [
      { text: "政策甲提出产业园区支持", chunk_ids: ["a-1"], quotes: ["支持产业园区建设。"], dimension: "支持方式", anchors: [] },
      { text: "政策甲提出人才补贴", chunk_ids: ["a-2"], quotes: ["给予人才补贴。"], dimension: "支持对象", anchors: [] },
      { text: "政策乙提出基础设施建设", chunk_ids: ["b-1"], quotes: ["加强基础设施建设。"], dimension: null, anchors: [] },
    ];
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: "",
      claims,
      evidence_refs: [
        makeEvidenceRef("P-A", "a-1", { title: "政策甲规划", area: "甲省", fyp: "十四五", section_path: "第一章 支持方式" }),
        makeEvidenceRef("P-A", "a-2", { title: "政策甲规划", area: "甲省", fyp: "十四五", section_path: "第二章 支持对象" }),
        makeEvidenceRef("P-B", "b-1", { title: "政策乙意见", area: "乙省", fyp: "十四五", section_path: "第一章" }),
      ],
    })} onEvidence={noEvidence} />);

    // One document entry per policy, with inline citations and distinct sections.
    expect(html.match(/class="qa-reference-number"/g)).toHaveLength(2);
    expect(html).toContain('data-chunk-id="a-1"');
    expect(html).toContain('data-chunk-id="a-2"');
    expect(html).toContain('data-chunk-id="b-1"');

    // Facts stay visible under their document; the expandable detail keeps each
    // claim's quote, section path and chunk locator.
    expect(html).toContain("政策甲提出产业园区支持");
    expect(html).toContain("政策甲提出人才补贴");
    expect(html).toContain("政策乙提出基础设施建设");
    expect(html).toContain("查看引文与章节");
    expect(html).toContain("支持产业园区建设。");
    expect(html).toContain("给予人才补贴。");
    expect(html).toContain("第二章 支持对象");
    expect(html).toContain("a-2");
  });

  it("keeps real report headings and paragraphs while dropping engineering scaffolding", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: [
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
      ].join("\n"),
      claims: [],
    })} onEvidence={noEvidence} />);

    expect(html).toContain("研究问题");
    expect(html).toContain("本轮研究关注产业园区支持政策。");
    expect(html).toContain("研究局限");
    expect(html).toContain("不代表已穷尽全部政策文献");
    expect(html).not.toContain("研究完成情况");
    expect(html).not.toContain("完成状态");
    expect(html).not.toContain("## ");
  });

  it("withholds insufficient comparison analysis while retaining linked facts", () => {
    const claim: Claim = { text: "已核验条款内容", chunk_ids: ["chunk-a"], quotes: ["原文依据句。"], dimension: "支持方式", anchors: [] };
    const structured: ComparisonResult = {
      objects: [
        { planid: "A", title: "方案甲", area: null, fyp: null, plantype_label: null },
        { planid: "B", title: "方案乙", area: null, fyp: null, plantype_label: null },
      ],
      dimensions: ["支持方式"],
      matrix: [{
        dimension: "支持方式",
        cells: [
          { cell_id: "d0:A", planid: "A", status: "found", text: claim.text, chunk_ids: claim.chunk_ids, quotes: claim.quotes },
          { cell_id: "d0:B", planid: "B", status: "insufficient", reason: "来源不足" },
        ],
        difference: { text: "不应渲染的不足分析文本", supporting_cells: ["d0:A"], kind: "analysis", status: "insufficient" },
        comparability: { comparable: false, reason: "时期口径不同" },
      }],
      comparability: { comparable: false, reason: "整体时期口径不同" },
      coverage_notes: [],
      analysis_gaps: [],
    };
    const html = renderToStaticMarkup(<Results result={makeResult({
      structured,
      claims: [claim],
      evidence_refs: [{ planid: "A", chunk_id: "chunk-a" } as EvidenceRef],
    })} onEvidence={noEvidence} />);

    expect(html).toContain("已核验条款内容");
    expect(html).toContain("原文 1");
    expect(html.match(/data-chunk-id="chunk-a"/g)).toHaveLength(1);
    expect(html.indexOf('data-chunk-id="chunk-a"')).toBeLessThan(html.indexOf("<tbody>"));
    expect(html).toContain("依据不足，无法判断");
    expect(html).not.toContain("整体时期口径不同");
    expect(html).not.toContain("口径需留意");
    expect(html).not.toContain("横向对照各政策在相同维度下的已见内容");
    expect(html).not.toContain("results-kicker");
    expect(html).not.toContain("不应渲染的不足分析文本");
  });
  it("places policy-specific source links beside titles and removes repeated links from analysis", () => {
    const ca: Claim = { text: "浙江目标", chunk_ids: ["a"], quotes: ["浙江原文"], dimension: "发展目标", anchors: [] };
    const cb: Claim = { text: "江苏目标", chunk_ids: ["b"], quotes: ["江苏原文"], dimension: "发展目标", anchors: [] };
    const structured: ComparisonResult = {
      objects: [
        { planid: "ZA", title: "浙江数字经济规划", area: "浙江省", fyp: "十四五", plantype_label: null },
        { planid: "JB", title: "江苏数字经济规划", area: "江苏省", fyp: "十四五", plantype_label: null },
      ],
      dimensions: ["发展目标"],
      matrix: [{
        dimension: "发展目标",
        cells: [
          { cell_id: "a", planid: "ZA", status: "found", text: ca.text, chunk_ids: ["a"], quotes: ca.quotes },
          { cell_id: "b", planid: "JB", status: "found", text: cb.text, chunk_ids: ["b"], quotes: cb.quotes },
        ],
        difference: { text: "两省目标不同", supporting_cells: ["a", "b"], kind: "analysis", status: "supported" },
        comparability: { comparable: false, reason: "不展示的口径提示" },
      }],
      comparability: { comparable: false, reason: "不展示的整体提示" },
      coverage_notes: [],
      analysis_gaps: [],
    };
    const html = renderToStaticMarkup(<Results result={makeResult({
      structured, claims: [ca, cb],
      evidence_refs: [
        { planid: "ZA", chunk_id: "a" } as EvidenceRef,
        { planid: "JB", chunk_id: "b" } as EvidenceRef,
      ],
    })} onEvidence={noEvidence} />);

    expect(html.match(/class="result-citation"/g)).toHaveLength(2);
    expect(html.indexOf('data-chunk-id="a"')).toBeLessThan(html.indexOf("planid ZA"));
    expect(html.indexOf('data-chunk-id="b"')).toBeLessThan(html.indexOf("planid JB"));
    expect(html.indexOf('data-chunk-id="b"')).toBeLessThan(html.indexOf("<tbody>"));
    expect(html).toContain("两省目标不同");
    expect(html).not.toContain("不展示的口径提示");
    expect(html).not.toContain("不展示的整体提示");
    expect(html).not.toContain('class="analysis-label">分析');
  });

  it("renders report outline as four distinct semantic heading levels", () => {
    const answer = [
      "密云县的发展环境总体呈现机遇与挑战并存的特征。",
      "",
      "一、主要发展机遇",
      "（一）奥运契机",
      "1、旅游市场",
      "（1）重点项目",
      "围绕规划开展旅游产业建设。",
      "",
      "二、面临挑战",
      "（一）基础设施条件",
      "需要加强基础设施建设。",
      "",
      "综上，上述判断仅限现有原文。",
    ].join("\n");
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa", answer, claims: [], structured: null,
    })} onEvidence={noEvidence} />);
    expect(html).toContain('<h2 class="qa-outline-heading qa-outline-level-1"');
    expect(html).toContain('<h3 class="qa-outline-heading qa-outline-level-2"');
    expect(html).toContain('<h4 class="qa-outline-heading qa-outline-level-3"');
    expect(html).toContain('<h5 class="qa-outline-heading qa-outline-level-4"');
    expect(html).toContain("一、</span><span>主要发展机遇");
    expect(html).toContain("（一）</span><span>奥运契机");
    expect(html).toContain("1、</span><span>旅游市场");
    expect(html).toContain("（1）</span><span>重点项目");
    expect(html).toContain("综上，上述判断仅限现有原文");
    expect(html).not.toContain("<ul");
  });

  it("leaves a single numeric fact as a paragraph instead of an outline heading", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa", answer: "提高就业率 1.5%，新增 200 个岗位。",
    })} onEvidence={noEvidence} />);
    expect(html).not.toContain("qa-outline-heading");
    expect(html).toContain("提高就业率 1.5%");
  });

});
