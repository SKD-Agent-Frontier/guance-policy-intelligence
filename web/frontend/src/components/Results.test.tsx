import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Results } from "./Results";
import type { Claim, ComparisonResult, DocumentIdentityFallback, EvidenceRef, RunResult } from "../types";

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

  it("shows scoped QA analysis and linked original buttons as interpretation, not facts", () => {
    const facts: Claim[] = [
      { text: "政策甲提到产业园区", chunk_ids: ["A"], quotes: ["产业园区"], dimension: null, anchors: [] },
      { text: "政策乙提到企业改造", chunk_ids: ["B"], quotes: ["企业改造"], dimension: null, anchors: [] },
    ];
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: "事实列举",
      claims: facts,
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
    expect(html).toContain("不代表推理已获语义验证");
    expect(html).toContain("事实 1：政策甲提到产业园区");
    expect(html).toContain("事实 2：政策乙提到企业改造");
    expect(html).toContain("data-chunk-id=\"A\"");
    expect(html).toContain("data-chunk-id=\"B\"");
  });

  it("renders failed QA status without an empty conclusion", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      status: "failed",
      error: { code: "OUTPUT_PARSE", message: "解析失败" },
      evidence_status: "none",
    })} onEvidence={noEvidence} />);
    expect(html).toContain("本轮研究未完成");
    expect(html).not.toContain("研究结论");
  });

  it("keeps plain QA layout unchanged without analysis", () => {
    const html = renderToStaticMarkup(<Results result={makeResult({
      task_type: "qa",
      answer: "原有政策事实",
      claims: [],
      structured: null,
    })} onEvidence={noEvidence} />);
    expect(html).toContain("研究结论");
    expect(html).not.toContain("研究分析");
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

});
