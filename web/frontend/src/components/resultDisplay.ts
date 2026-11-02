import type { Claim, EvidenceRef } from "../types";

export interface EvidenceChunk {
  chunkId: string;
  reference: EvidenceRef | null;
}

export interface ClaimEvidence {
  claim: Claim;
  index: number;
  quotes: string[];
  chunks: EvidenceChunk[];
}

export interface DocumentEvidenceGroup {
  planid: string | null;
  title: string;
  area: string | null;
  fyp: string | null;
  /** Single main original-text entry for this document. */
  primaryChunkId: string | null;
  claims: ClaimEvidence[];
}

/**
 * Merge claims by the policy document (planid) they cite. The merged view keeps
 * one primary original-text entry per document while every claim keeps its own
 * quotes and chunk locators for the expandable evidence detail.
 */
export function groupClaimsByDocument(
  claims: Claim[],
  evidenceRefs: EvidenceRef[] | null | undefined,
): DocumentEvidenceGroup[] {
  const byChunkId = new Map((evidenceRefs ?? []).map((reference) => [reference.chunk_id, reference]));
  const groups = new Map<string, DocumentEvidenceGroup>();

  claims.forEach((claim, index) => {
    const chunks = (claim.chunk_ids ?? []).map((chunkId) => ({
      chunkId,
      reference: byChunkId.get(chunkId) ?? null,
    }));
    const firstReference = chunks.find((chunk) => chunk.reference)?.reference ?? null;
    const planid = firstReference?.planid ?? null;
    const key = planid ?? "__unmatched__";
    let group = groups.get(key);
    if (!group) {
      group = {
        planid,
        title: firstReference?.title?.trim() ?? "",
        area: firstReference?.area ?? null,
        fyp: firstReference?.fyp ?? null,
        primaryChunkId: null,
        claims: [],
      };
      groups.set(key, group);
    }
    group.claims.push({ claim, index, quotes: claim.quotes ?? [], chunks });
    if (!group.primaryChunkId) {
      group.primaryChunkId = chunks.find((chunk) => chunk.reference)?.chunkId ?? null;
    }
  });

  return [...groups.values()];
}

// Raw delivery state, gap codes and fixed hedging text belong to diagnostics,
// not to the reader-facing result body.
const INTERNAL_STATUS_PATTERN =
  /research_(?:quality|completion|delivery)|research\s*(?:状态|质量|完成)|claims_removed_or_answer_rebuilt|evidence_insufficient|verified_claims_missing|verified_evidence_missing|audited_(?:report|analysis)_missing|source_excerpts_only|multi_document_required|object_scope_mismatch|execution_not_completed|evidence_coverage_insufficient|\b(?:partial|insufficient|completion)\b/i;

const INTERNAL_SECTION_HEADING =
  /^(?:研究完成情况|完成情况|完成度|核验状态|核验信息|核验结果|诊断信息|研究质量|质量与核验|交付状态|状态说明)/;

// Only engineering prompts / gap-list phrasing belongs here. Short user-facing
// refusal or limitation sentences from the backend (e.g. “未找到某项内容不代表
// 政策中不存在该项内容”) must stay in the answer body.
const AGENT_NOTE_LINE =
  /(?:可提取以下|仍需单独评估|仅展示已经核验|尚未完成的研究分析|不得视为结论|研究问题尚未完整回答。?未满足[：:]|完成度缺口[：:]|未满足维度[：:])/;

const STATUS_LINE = /^(?:完成状态|完成度缺口|多文献要求|未满足维度|缺少经审核分析的维度|缺少有效证据的文献|未满足|缺口|说明：以上)/;

const APPENDIX_TITLE = /^研究完成情况\s*[:：]?$/;

const MARKDOWN_HEADING = /^(#{1,4})\s+(.+?)\s*#*$/;

const normalizeFactText = (value: string): string =>
  value
    .replace(/^[-*•·]\s*/, "")
    .replace(/^#{1,4}\s+/, "")
    .replace(/[\s\u3000]+/g, "")
    .replace(/[。．.；;：:！!？?，,、"'“”‘’（）()【】《》]/g, "");

/** Whether a line carries raw delivery status / gap codes instead of readable content. */
export function containsInternalStatus(text: string): boolean {
  return INTERNAL_STATUS_PATTERN.test(text);
}

interface AnswerSection {
  level: number;
  heading: string;
  lines: string[];
}

function splitAnswerSections(source: string): { preamble: string[]; sections: AnswerSection[] } {
  const preamble: string[] = [];
  const sections: AnswerSection[] = [];
  let current: AnswerSection | null = null;
  for (const line of source.split("\n")) {
    // The completion appendix is appended at the very end of the answer.
    if (APPENDIX_TITLE.test(line.trim())) break;
    const heading = MARKDOWN_HEADING.exec(line.trim());
    if (heading) {
      current = { level: heading[1].length, heading: heading[2].trim(), lines: [] };
      sections.push(current);
      continue;
    }
    if (current) current.lines.push(line);
    else preamble.push(line);
  }
  return { preamble, sections };
}

/** Remove the hedge a report heading carries, e.g. “（非完整研究结论）”. */
function normalizeHeading(heading: string): string {
  const stripped = heading
    .replace(/[（(][^）)]*(?:非完整|部分|未经审核|尚未完成)[^）)]*[）)]\s*$/, "")
    .trim();
  return stripped || heading;
}

function cleanLines(lines: string[], factTexts: Set<string>): string {
  const kept: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      kept.push("");
      continue;
    }
    if (AGENT_NOTE_LINE.test(trimmed) || STATUS_LINE.test(trimmed) || containsInternalStatus(trimmed)) continue;
    const normalized = normalizeFactText(trimmed);
    // Verified claims are rendered with their own source group; do not repeat
    // the same fact as duplicated report prose.
    if (normalized && factTexts.has(normalized)) continue;
    kept.push(line);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

/**
 * Strip agent/delivery scaffolding from `result.answer` before it reaches the
 * reader: engineering prompts, internal completion state, raw gap codes and
 * duplicated fact bullets are removed, while real report headings and
 * paragraphs (研究问题 / 研究局限 / 参考文献 …) are preserved.
 */
export function prepareAnswerBody(answer: string | null | undefined, claims: Claim[]): string {
  const source = (answer ?? "").replace(/\r\n?/g, "\n");
  if (!source.trim()) return "";

  const factTexts = new Set<string>();
  for (const claim of claims) {
    for (const text of [claim.text, ...(claim.quotes ?? [])]) {
      const normalized = normalizeFactText(text ?? "");
      if (normalized) factTexts.add(normalized);
    }
  }

  const { preamble, sections } = splitAnswerSections(source);
  const blocks: string[] = [];
  const preambleText = cleanLines(preamble, factTexts);
  if (preambleText) blocks.push(preambleText);

  for (const section of sections) {
    if (INTERNAL_SECTION_HEADING.test(section.heading)) continue;
    const body = cleanLines(section.lines, factTexts);
    // A section reduced to duplicated facts has no visible content left.
    if (!body) continue;
    const level = Math.min(section.level, 4);
    blocks.push(`${"#".repeat(level)} ${normalizeHeading(section.heading)}\n\n${body}`);
  }

  if (!blocks.length && sections.length > 0) {
    // Some reviewed reports legitimately organize the answer as atomic fact
    // sections. Do not blank the whole research body merely because every
    // paragraph is also shown in the evidence list; keep the report text.
    return source
      .split("\n")
      .filter((line) => {
        const trimmed = line.trim();
        return trimmed && !AGENT_NOTE_LINE.test(trimmed) && !STATUS_LINE.test(trimmed)
          && !containsInternalStatus(trimmed);
      })
      .join("\n")
      .trim();
  }

  return blocks.join("\n\n").trim();
}
