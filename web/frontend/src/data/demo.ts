import type {
  Claim,
  ComparisonResult,
  EvidenceRef,
  ExtractResult,
  OrganizeResult,
  ResearchRequest,
  RunResult,
  TaskType,
} from "../types";

export type DemoScenario = "success" | "insufficient" | "empty" | "failed" | "limit_reached";

interface DemoPolicy {
  planid: string;
  demoIndex: number;
  title: string;
  area: string;
  fyp: string;
  plantype_label: string;
  topic: string;
  fact: string;
  quote: string;
}

const DEMO_POLICIES: DemoPolicy[] = [
  {
    planid: "DEMO-A",
    demoIndex: 0,
    title: "示例人才支持方案 A（演示）",
    area: "示例地区甲",
    fyp: "演示规划期",
    plantype_label: "演示专项方案",
    topic: "演示人才支持",
    fact: "示例方案 A 提供培训补助。",
    quote: "示例方案 A 提供培训补助。",
  },
  {
    planid: "DEMO-B",
    demoIndex: 1,
    title: "示例人才支持方案 B（演示）",
    area: "示例地区乙",
    fyp: "演示规划期",
    plantype_label: "演示专项方案",
    topic: "演示人才支持",
    fact: "示例方案 B 提供创新项目辅导。",
    quote: "示例方案 B 提供创新项目辅导。",
  },
  {
    planid: "DEMO-C",
    demoIndex: 2,
    title: "示例人才支持方案 C（演示）",
    area: "示例地区丙",
    fyp: "演示规划期",
    plantype_label: "演示专项方案",
    topic: "演示人才支持",
    fact: "示例方案 C 支持短期使用共享空间。",
    quote: "示例方案 C 支持短期使用共享空间。",
  },
];

const DEFAULT_DIMENSIONS = ["适用对象", "政策工具", "量化目标", "责任主体", "期限", "资金规模"];
const DEFAULT_COMPARISON_DIMENSIONS = ["演示支持方式", "演示适用对象", "演示适用条件"];
const AREA_SUFFIXES = ["甲", "乙", "丙"];
const EMPTY_STRUCTURED = Object.freeze({});

function requestTargets(request: ResearchRequest): string[] {
  if (request.targets?.length) return [...request.targets];
  if (request.task_type === "organize") return DEMO_POLICIES.map((policy) => policy.planid);
  if (request.task_type === "compare" || request.task_type === "compare_plans") {
    return DEMO_POLICIES.slice(0, 2).map((policy) => policy.planid);
  }
  return [DEMO_POLICIES[0].planid];
}

function policyFor(planid: string, index: number): DemoPolicy {
  const known = DEMO_POLICIES.find((policy) => policy.planid === planid);
  if (known) return known;
  const template = DEMO_POLICIES[index % DEMO_POLICIES.length];
  return {
    ...template,
    planid,
    demoIndex: index % DEMO_POLICIES.length,
    title: `示例政策 ${planid}（演示）`,
    area: `演示地区${AREA_SUFFIXES[index % AREA_SUFFIXES.length]}`,
  };
}

function quoteFor(policy: DemoPolicy, dimension: string | null): string {
  if (!dimension) return policy.quote;
  const index = policy.demoIndex;
  const service = ["培训补助", "创新项目辅导", "共享空间使用"][index % 3];
  if (/对象/.test(dimension)) return `${policy.area}的示例服务对象可申请${service}。`;
  if (/条件|申报/.test(dimension)) return `示例条件：申请人需提交${["培训计划", "项目简介", "团队说明"][index % 3]}。`;
  if (/资金|金额|预算|标准/.test(dimension)) return `示例标准：单项演示支持额度为${[2, 3, 4][index % 3]}万元。`;
  if (/期限|时间|周期/.test(dimension)) return `示例期限：支持周期为${[3, 6, 9][index % 3]}个月。`;
  if (/流程|办理/.test(dimension)) return `示例流程：材料提交后进行${["形式审查", "项目评议", "资格核验"][index % 3]}。`;
  if (/措施|工具|支持/.test(dimension)) return `${policy.title}提供${service}。`;
  return `示例维度“${dimension}”：${policy.area}对应${service}。`;
}

function demoClaim(policy: DemoPolicy, dimension: string | null, slot = "0"): Claim {
  const quote = quoteFor(policy, dimension);
  return {
    text: quote,
    chunk_ids: [`demo-chunk-${policy.planid}-${slot}`],
    quotes: [quote],
    dimension,
    anchors: [],
  };
}

function planidFromChunkId(chunkId: string): string {
  return chunkId.replace(/^demo-chunk-/, "").replace(/-\d+$/, "");
}

function demoEvidence(policy: DemoPolicy, chunkId: string, quote: string): EvidenceRef {
  return {
    document_id: `demo-document-${policy.planid}`,
    document_version_id: `demo-version-${policy.planid}`,
    generation_id: "demo-generation",
    source_hash: null,
    section_id: "demo-section",
    section_char_start: 0,
    section_char_end: quote.length,
    offset_basis: "demo_only",
    offset_unit: "unicode_codepoint",
    body_origin: 0,
    contract_version: "demo-v1",
    quality_warnings: ["演示来源：内容为合成文本，不对应真实政策"],
    planid: policy.planid,
    chunk_id: chunkId,
    parent_id: null,
    title: policy.title,
    area: policy.area,
    fyp: policy.fyp,
    admin_level: "演示级别",
    plantype_label: policy.plantype_label,
    section_idx: 0,
    section_path: "演示章节",
    char_start: 0,
    char_end: quote.length,
    chunk_char_start: 0,
    chunk_char_end: quote.length,
    truncated: false,
    source: "kb_read",
    observed_ranges: [[0, quote.length]],
    complete: true,
    out_of_section: false,
  };
}

function organizeResult(policies: DemoPolicy[], claims: Claim[], insufficient: boolean, request: ResearchRequest): OrganizeResult {
  const claimIndexes = claims.map((_claim, index) => index);
  const claimsFor = (policy: DemoPolicy) => claims
    .map((claim, index) => ({ claim, index }))
    .filter(({ claim }) => claim.chunk_ids.some((chunkId) => planidFromChunkId(chunkId) === policy.planid));
  const summarizedCount = policies.filter((policy) => claimsFor(policy).length > 0).length;
  const scope: OrganizeResult["scope"] = {
    topic: "演示人才支持",
    areas: [...new Set(policies.map((policy) => policy.area))],
    fyps: ["演示规划期"],
    plantypes: ["演示专项方案"],
    categories: [],
    dimensions: request.dimensions ? [...request.dimensions] : [],
    targets: request.targets?.length ? policies.map((policy) => policy.planid) : null,
    effective: {
      queries: [{ query: "演示人才支持", status: "ok", hits: policies.length, new_documents: policies.length }],
      area: null,
      fyp: null,
      top_k: policies.length,
    },
    post_filters: [],
    unapplied: [],
  };
  return {
    schema_version: "organize/1",
    scope,
    coverage: {
      documents_found: policies.length,
      documents_after_filter: policies.length,
      documents_read: insufficient ? Math.min(1, policies.length) : policies.length,
      documents_summarized: summarizedCount,
      chunks_read: claims.length,
      limits: insufficient ? ["demo_partial_coverage"] : [],
      note: "演示数据仅用于展示结果界面；覆盖范围和内容均为合成示例，不代表真实检索或政策结论。",
    },
    policies: policies.map((policy) => {
      const matchingClaims = claimsFor(policy);
      const indexes = matchingClaims.map(({ index: claimIndex }) => claimIndex);
      return {
        planid: policy.planid,
        title: policy.title,
        area: policy.area,
        fyp: policy.fyp,
        admin_level: "演示级别",
        plantype_label: policy.plantype_label,
        read_status: matchingClaims.length ? "summarized" : "not_read",
        relevance: matchingClaims.length ? "medium" : null,
        relevance_note: "演示数据：仅用于交互预览。",
        topics: matchingClaims.length ? [policy.topic] : [],
        summary: matchingClaims.length ? matchingClaims.map(({ claim }) => claim.text).join("；") : null,
        claim_indexes: indexes,
        chunk_ids_hit: matchingClaims.flatMap(({ claim }) => claim.chunk_ids),
        chunks_read: matchingClaims.length,
        full_text_covered: false,
        read_attempts: matchingClaims.flatMap(({ claim }) => claim.chunk_ids.map((chunk_id) => ({ chunk_id, status: "ok" }))),
      };
    }),
    groups: claims.length
      ? [{ topic: "演示人才支持", planids: policies.filter((policy) => claimsFor(policy).length > 0).map((policy) => policy.planid), claim_indexes: claimIndexes }]
      : [],
    verification: {
      policy_facts: "claims_only",
      analysis_fields: ["relevance", "relevance_note", "topics", "groups"],
      note: "演示数据：所有内容均为合成示例，不构成真实政策事实。",
    },
  };
}

function comparisonResult(policies: DemoPolicy[], claims: Claim[], includeSchemeAnalysis: boolean, dimensions: string[]): ComparisonResult {
  const claimFor = (policy: DemoPolicy, dimensionIndex: number) => claims.find((claim) =>
    claim.chunk_ids.includes(`demo-chunk-${policy.planid}-${dimensionIndex}`));
  const matrix = dimensions.map((dimension, dimensionIndex) => {
    const cells = policies.map((policy) => {
      const claim = claimFor(policy, dimensionIndex);
      return {
        cell_id: `d${dimensionIndex}:${policy.planid}`,
        planid: policy.planid,
        status: claim ? "found" as const : "insufficient" as const,
        ...(claim
          ? { text: claim.quotes[0], chunk_ids: [...claim.chunk_ids], quotes: [...claim.quotes], source_contexts: ["演示章节"] }
          : { reason: "演示结果中未提供该对象的示例证据。" }),
      };
    });
    const supportingCells = cells.filter((cell) => cell.status === "found").map((cell) => cell.cell_id);
    const allFound = cells.length > 0 && cells.every((cell) => cell.status === "found");
    return {
      dimension,
      cells,
      difference: {
        text: "对照这些示例条款，支持内容和适用范围有所不同。",
        supporting_cells: supportingCells,
        kind: "analysis" as const,
        scope: "supporting_cells_only" as const,
        status: allFound ? "supported" as const : "insufficient" as const,
      },
      comparability: { comparable: allFound, reason: "仅用于演示表格布局，不代表真实可比性。" },
    };
  });
  const allFound = matrix.every((row) => row.cells.every((cell) => cell.status === "found"));
  const result: ComparisonResult = {
    objects: policies.map(({ planid, title, area, fyp, plantype_label }) => ({
      planid,
      title,
      area,
      fyp,
      plantype_label,
    })),
    dimensions,
    matrix,
    comparability: { comparable: allFound, reason: "仅用于演示表格布局，不代表真实可比性。" },
    coverage_notes: allFound ? [] : ["演示数据不完整，仅用于显示部分结果状态。"],
    analysis_gaps: [],
  };
  if (includeSchemeAnalysis) {
    const roleDimensions = [0, Math.min(1, dimensions.length - 1), Math.min(2, dimensions.length - 1)];
    result.scheme_analysis = policies.map((policy) => {
      const cellIds = roleDimensions.map((dimensionIndex) => `d${dimensionIndex}:${policy.planid}`);
      const analysis = (text: string, dimensionIndex: number) => ({
        text,
        supporting_cells: [cellIds[dimensionIndex]],
        kind: "analysis" as const,
        scope: "supporting_cells_only" as const,
      });
      return {
        planid: policy.planid,
        advantages: claimFor(policy, roleDimensions[0]) ? [analysis("这项示例展示一种演示支持内容。", 0)] : [],
        limitations: claimFor(policy, roleDimensions[1]) ? [analysis("演示适用范围仅限示例设定的对象。", 1)] : [],
        applicability: claimFor(policy, roleDimensions[2])
          ? [{ ...analysis(claimFor(policy, roleDimensions[2])?.quotes[0] ?? policy.quote, 2), derivation: "extractive_conditions" as const }]
          : [],
      };
    });
    result.evaluation_criteria = [];
  }
  return result;
}

function extractResult(policies: DemoPolicy[], claims: Claim[], request: ResearchRequest): ExtractResult {
  const dimensions = request.dimensions?.length ? [...request.dimensions] : [...DEFAULT_DIMENSIONS];
  return {
    dimensions,
    documents: policies.map(({ planid, title, area, fyp }) => ({ planid, title, area, fyp })),
    elements: claims,
    missing: dimensions
      .filter((dimension) => !claims.some((claim) => claim.dimension === dimension))
      .map((dimension) => ({ dimension, reason: "not_retrieved" })),
  };
}

function structuredFor(
  taskType: TaskType,
  policies: DemoPolicy[],
  claims: Claim[],
  request: ResearchRequest,
  insufficient: boolean,
): RunResult["structured"] {
  if (taskType === "qa") return null;
  if (taskType === "extract") return extractResult(policies, claims, request);
  if (taskType === "organize") return organizeResult(policies, claims, insufficient, request);
  if (taskType === "compare" || taskType === "compare_plans") {
    const dimensions = request.dimensions?.length ? [...request.dimensions] : [...DEFAULT_COMPARISON_DIMENSIONS];
    return comparisonResult(policies, claims, taskType === "compare_plans", dimensions);
  }
  return EMPTY_STRUCTURED;
}

export function createDemoResult(request: ResearchRequest, scenario: DemoScenario = "success"): RunResult {
  const taskType = request.task_type;
  const ids = requestTargets(request);
  const policies = ids.map(policyFor);
  const dimensions = taskType === "extract"
    ? (request.dimensions?.length ? [...request.dimensions] : [...DEFAULT_DIMENSIONS])
    : taskType === "compare" || taskType === "compare_plans"
      ? (request.dimensions?.length ? [...request.dimensions] : [...DEFAULT_COMPARISON_DIMENSIONS])
      : [];
  const visiblePolicies = scenario === "insufficient" ? policies.slice(0, Math.min(1, policies.length)) : policies;
  const claims = scenario === "success" || scenario === "insufficient"
    ? taskType === "extract"
      ? visiblePolicies.flatMap((policy) => dimensions
          .slice(0, scenario === "insufficient" ? 1 : undefined)
          .map((dimension, index) => demoClaim(policy, dimension, `${index}`)))
      : taskType === "compare" || taskType === "compare_plans"
        ? dimensions.flatMap((_dimension, dimensionIndex) => visiblePolicies.map((policy) =>
            demoClaim(policy, dimensions[dimensionIndex], `${dimensionIndex}`)))
        : visiblePolicies.map((policy) => demoClaim(policy, null))
    : [];
  const failure = scenario === "failed" || scenario === "limit_reached";
  const empty = scenario === "empty";
  const failed = scenario === "failed";
  const limited = scenario === "limit_reached";
  const status = failed ? "failed" : limited ? "limit_reached" : "completed";
  const structured = failure || empty
    ? (taskType === "qa" ? null : EMPTY_STRUCTURED)
    : structuredFor(taskType, policies, claims, request, scenario === "insufficient");

  return {
    run_id: `demo-run-${crypto.randomUUID()}`,
    session_id: "demo-session",
    status,
    evidence_status: empty ? "none" : scenario === "insufficient" || limited ? "insufficient" : failed ? "none" : "verified",
    answer: failed
      ? "演示请求失败。"
      : limited
        ? "演示已达到处理上限。"
        : empty
          ? "本轮没有可展示的结果。"
          : scenario === "insufficient"
            ? "证据不足，当前仅展示部分示例。"
            : "示例结果用于展示界面；未检索或回答原始问题。",
    claims,
    evidence_refs: claims.flatMap((claim) => claim.chunk_ids.flatMap((chunkId) => {
      const policy = policies.find((candidate) => candidate.planid === planidFromChunkId(chunkId));
      return policy ? [demoEvidence(policy, chunkId, claim.quotes[0] ?? claim.text)] : [];
    })),
    dropped_chunk_ids: [],
    error: failed || limited
      ? { code: failed ? "DEMO_FAILURE" : "MAX_TURNS", message: "演示状态：请求未产生完整结果。" }
      : null,
    meta: {
      elapsed_ms: 900,
      tool_calls: [],
      is_mock: true,
      demo_question: request.question,
      demo_scenario: scenario,
    },
    warnings: ["仅为演示数据：所有政策名称、事实、引文及来源均为合成内容。"],
    task_type: taskType,
    structured,
    route: {
      kind: ids.length > 1 ? "objects" : "single_object",
      planids: ids,
      titles: [],
      areas: [],
      fyps: [],
      sub_query: "演示路由，不执行真实检索。",
      targets: ids,
    },
  };
}
