import { Fragment, type ReactNode } from "react";
import type { Claim, EvidenceRef, QAAnalysisResult, RunResult } from "../types";
import { AnswerOutline } from "./AnswerOutline";
import { prepareAnswerBody } from "./resultDisplay";

type Source = { key: string; number: number; reference: EvidenceRef; chunks: EvidenceRef[] };
type Link = { source: Source; chunkId: string };
type Passage = { text: string; chunkIds: string[] };

/** Only references explicitly linked by the backend can become citations. */
function collectSources(claims: Claim[], refs: EvidenceRef[]) {
  const used = new Set(claims.flatMap((claim) => claim.chunk_ids ?? []));
  const sources: Source[] = [];
  const byKey = new Map<string, Source>();
  const byChunk = new Map<string, Source>();
  for (const ref of refs) {
    if (!used.has(ref.chunk_id) || !ref.planid || !ref.chunk_id) continue;
    const key = JSON.stringify([ref.planid, ref.document_id ?? null, ref.generation_id ?? null]);
    let source = byKey.get(key);
    if (!source) {
      source = { key, number: sources.length + 1, reference: ref, chunks: [] };
      byKey.set(key, source);
      sources.push(source);
    }
    if (!source.chunks.some((item) => item.chunk_id === ref.chunk_id)) source.chunks.push(ref);
    byChunk.set(ref.chunk_id, source);
  }
  return { sources, byChunk };
}

function linksFor(chunkIds: string[], byChunk: Map<string, Source>): Link[] {
  const links = new Map<string, Link>();
  for (const chunkId of chunkIds) {
    const source = byChunk.get(chunkId);
    if (source && !links.has(source.key)) links.set(source.key, { source, chunkId });
  }
  return [...links.values()].sort((a, b) => a.source.number - b.source.number);
}

function CitationMarks({ links, onEvidence }: { links: Link[]; onEvidence: (chunkId: string) => void }) {
  return <>{links.map(({ source, chunkId }) => <sup className="qa-inline-citation" key={`${source.key}-${chunkId}`}><button type="button" className="result-citation" data-chunk-id={chunkId} aria-label={`查看引用 ${source.number}：${source.reference.title || "政策原文"}`} onClick={() => onEvidence(chunkId)}>[{source.number}]</button></sup>)}</>;
}

function reportPassages(result: RunResult, claims: Claim[]): Passage[] {
  const structured = result.structured as unknown;
  if (!structured || typeof structured !== "object" || !("schema_version" in structured)) return [];
  if (structured.schema_version === "qa-analysis/1") {
    return (structured as QAAnalysisResult).analyses.map((analysis) => ({
      text: analysis.text,
      chunkIds: analysis.supporting_chunk_ids.filter((id) => analysis.supporting_claim_indexes.some((index) => claims[index]?.chunk_ids.includes(id))),
    }));
  }
  if (structured.schema_version !== "qa-report/1" || !("blocks" in structured) || !Array.isArray(structured.blocks)) return [];
  return structured.blocks.flatMap((value: unknown) => {
    if (!value || typeof value !== "object" || !("text" in value) || typeof value.text !== "string" || !("supporting_chunk_ids" in value) || !Array.isArray(value.supporting_chunk_ids) || !("supporting_claim_indexes" in value) || !Array.isArray(value.supporting_claim_indexes)) return [];
    const indexes = value.supporting_claim_indexes.filter((index: unknown): index is number => Number.isInteger(index) && typeof index === "number" && index >= 0);
    const ids = value.supporting_chunk_ids.filter((id: unknown): id is string => typeof id === "string" && indexes.some((index: number) => claims[index]?.chunk_ids.includes(id)));
    return [{ text: value.text, chunkIds: ids }];
  });
}

export function QAAnswer({ result, onEvidence }: { result: RunResult; onEvidence: (chunkId: string) => void }) {
  const claims = result.claims ?? [];
  const answer = prepareAnswerBody(result.answer, []);
  const { sources, byChunk } = collectSources(claims, result.evidence_refs ?? []);
  const passages = [...reportPassages(result, claims), ...claims.map((claim) => ({ text: claim.text, chunkIds: claim.chunk_ids }))]
    .filter((passage) => passage.text.trim() && linksFor(passage.chunkIds, byChunk).length > 0)
    .sort((a, b) => b.text.length - a.text.length);

  const renderText = (text: string): ReactNode => {
    const parts: ReactNode[] = [];
    let cursor = 0;
    while (cursor < text.length) {
      let match: Passage | undefined;
      let position = text.length;
      for (const passage of passages) {
        const candidate = text.indexOf(passage.text, cursor);
        if (candidate >= 0 && candidate < position) { match = passage; position = candidate; }
      }
      if (!match) { parts.push(text.slice(cursor)); break; }
      parts.push(text.slice(cursor, position));
      const sameText = passages.filter((passage) => passage.text === match!.text);
      const links = linksFor(sameText.flatMap((passage) => passage.chunkIds), byChunk);
      parts.push(<Fragment key={`${position}-${match.text}`}>{match.text}<CitationMarks links={links} onEvidence={onEvidence} /></Fragment>);
      cursor = position + match.text.length;
    }
    return parts;
  };
  const missingClaims = claims.filter((claim) => claim.text.trim() && !answer.includes(claim.text));
  const analysis = result.structured && "schema_version" in result.structured && result.structured.schema_version === "qa-analysis/1" ? result.structured as QAAnalysisResult : null;
  const missingAnalysis = analysis?.analyses.filter((entry) => !answer.includes(entry.text)) ?? [];

  return <div className="results-content results-content--qa qa-direct-answer">
    {answer && <AnswerOutline text={answer} renderText={renderText} />}
    {missingClaims.map((claim, index) => <p className="qa-direct-fact" key={`${index}-${claim.text}`}>{claim.text}<CitationMarks links={linksFor(claim.chunk_ids, byChunk)} onEvidence={onEvidence} /></p>)}
    {missingAnalysis.length > 0 && <section className="qa-direct-analysis" aria-label="研究分析"><p className="qa-analysis-disclaimer">以下为基于已核验事实的分析，不是政策原文。</p>{missingAnalysis.map((item, index) => <p key={index}>{item.text}<CitationMarks links={linksFor(item.supporting_chunk_ids.filter((id) => item.supporting_claim_indexes.some((claimIndex) => claims[claimIndex]?.chunk_ids.includes(id))), byChunk)} onEvidence={onEvidence} />{item.scope_note && <small> {item.scope_note}</small>}</p>)}</section>}
    {!answer && claims.length === 0 && <p className="results-notice">本轮没有可核验的政策事实。</p>}
    {sources.length > 0 && <section className="qa-reference-list" aria-label="引用文件"><h2>引用文件</h2><ol>{sources.map((source) => {
      const quotes = [...new Set(claims.filter((claim) => claim.chunk_ids.length > 0 && claim.chunk_ids.every((id) => byChunk.get(id)?.key === source.key)).flatMap((claim) => claim.quotes ?? []))];
      return <li key={source.key}><span className="qa-reference-number">[{source.number}]</span><div><button type="button" className="result-citation qa-reference-title" data-chunk-id={source.reference.chunk_id} onClick={() => onEvidence(source.reference.chunk_id)}>{source.reference.title || "政策原文"}</button><p>{[source.reference.area, source.reference.fyp].filter(Boolean).join(" · ")}</p><details><summary>查看引文与章节</summary>{quotes.map((quote) => <blockquote key={quote}>{quote}</blockquote>)}{source.chunks.map((ref) => <button type="button" className="result-citation qa-reference-section" data-chunk-id={ref.chunk_id} key={ref.chunk_id} onClick={() => onEvidence(ref.chunk_id)}>{ref.section_path || "原文片段"}</button>)}</details></div></li>;
    })}</ol></section>}
  </div>;
}
