import { ArrowUpRight, BookOpen, ChevronRight, FileText, GitCompareArrows, Layers3, Sparkles } from "lucide-react";
import type {
  Claim,
  ComparisonAnalysis,
  ComparisonCell,
  ComparisonResult,
  EvidenceRef,
  ExtractResult,
  OrganizePolicy,
  OrganizeResult,
  QAAnalysisResult,
  RunResult,
} from "../types";
import { AnswerOutline } from "./AnswerOutline";
import "./results.css";

interface ResultsProps {
  result: RunResult;
  onEvidence: (chunkId: string) => void;
}

const valueOrMissing = (value: string | null | undefined) => value?.trim() || "未提供";

function CitationButtons({
  claim,
  onEvidence,
  compact = false,
}: {
  claim: Claim | undefined;
  onEvidence: (chunkId: string) => void;
  compact?: boolean;
}) {
  if (!claim?.chunk_ids.length) return <span className="result-no-citation">暂无关联证据</span>;
  return (
    <span className={`result-citations${compact ? " result-citations--compact" : ""}`}>
      {claim.chunk_ids.map((chunkId, index) => (
        <button className="result-citation" data-chunk-id={chunkId} key={`${chunkId}-${index}`} onClick={() => onEvidence(chunkId)} type="button">
          <BookOpen aria-hidden="true" size={13} />
          <span>原文 {index + 1}</span>
          <ArrowUpRight aria-hidden="true" size={12} />
        </button>
      ))}
    </span>
  );
}

function Fact({ claim, onEvidence, showCitations = true }: {
  claim: Claim;
  onEvidence: (chunkId: string) => void;
  showCitations?: boolean;
}) {
  return <div className="result-fact"><p>{claim.text}</p>{showCitations && <CitationButtons claim={claim} onEvidence={onEvidence} />}</div>;
}

/** Policy header links: only cite verified references used by this policy's matrix cells. */
function ComparisonPolicySources({ planid, rows, claims, evidenceRefs, onEvidence }: {
  planid: string;
  rows: ComparisonResult["matrix"];
  claims: Claim[];
  evidenceRefs: EvidenceRef[];
  onEvidence: (chunkId: string) => void;
}) {
  const cited = new Set(claims.flatMap((claim) => claim.chunk_ids));
  const available = new Set(evidenceRefs.filter((ref) => ref.planid === planid).map((ref) => ref.chunk_id));
  const chunkIds = [...new Set(rows.flatMap((row) => row.cells.flatMap((cell) =>
    cell.planid === planid && cell.status === "found" ? cell.chunk_ids ?? [] : [],
  )))].filter((id) => cited.has(id) && available.has(id));
  if (!chunkIds.length) return null;
  return <span className="comparison-title-sources result-citations result-citations--compact">
    {chunkIds.map((id, index) => <button className="result-citation" data-chunk-id={id} key={id}
      onClick={() => onEvidence(id)} type="button" aria-label={`查看政策原文 ${index + 1}`}>
      <BookOpen aria-hidden="true" size={15} /><span>原文 {index + 1}</span><ArrowUpRight aria-hidden="true" size={13} />
    </button>)}
  </span>;
}

function Header({ title, note }: { title: string; note?: string }) {
  return <header className="results-heading"><h1>{title}</h1>{note && <p>{note}</p>}</header>;
}

function AnswerFallback({ result, onEvidence }: ResultsProps) {
  const claims = result.claims ?? [];
  return (
    <section className="results-fallback">
      {result.answer && <div className="results-answer"><AnswerOutline text={result.answer} /></div>}
      {claims.length > 0 && <div className="results-claim-list">{claims.map((claim, index) => <Fact key={`${index}-${claim.text}`} claim={claim} onEvidence={onEvidence} />)}</div>}
      {!result.answer && claims.length === 0 && <p className="results-empty">本次任务没有可展示的结构化结果。</p>}
    </section>
  );
}

function QAResults({ result, onEvidence }: ResultsProps) {
  const claims = result.claims ?? [];
  if (result.status !== "completed") {
    // 失败不是“没有政策结论”。不展示空的研究结论卡片，避免误导。
    return <div className="results-content results-content--qa">
      <section className="results-notice" role="alert">
        <strong>本轮研究未完成</strong>
        <p>{result.error?.message || "研究执行未完成，请查看下方诊断信息。"}</p>
      </section>
    </div>;
  }
  return (
    <div className="results-content results-content--qa">
      <Header title="研究结论" />
      {result.answer && <div className="qa-answer"><div className="qa-answer-icon"><Sparkles aria-hidden="true" size={16} /></div><AnswerOutline text={result.answer} /></div>}
      {result.structured && "schema_version" in result.structured
        && result.structured.schema_version === "qa-analysis/1"
        && (result.structured as QAAnalysisResult).analyses.length > 0 && (
          <section className="qa-analysis" aria-label="研究分析">
            <h2>研究分析</h2>
            <p className="qa-analysis-disclaimer">以下为基于已读事实的归纳或推理，不是政策原文，也不代表推理已获语义验证。</p>
            {(result.structured as QAAnalysisResult).analyses.map((analysis, index) => (
              <article className="qa-analysis-item" key={index}>
                <span className="analysis-label">分析</span>
                <p>{analysis.text}</p>
                <small>{analysis.scope_note}</small>
                <div className="qa-analysis-sources">
                  <span>依据事实</span>
                  {analysis.supporting_claim_indexes.map(claimIndex => {
                    const claim = claims[claimIndex];
                    return claim ? <div key={claimIndex} className="qa-analysis-support">
                      <span>事实 {claimIndex + 1}：{claim.text}</span>
                      <CitationButtons claim={claim} onEvidence={onEvidence} compact />
                    </div> : null;
                  })}
                </div>
              </article>
            ))}
          </section>
        )}
      {claims.length > 0 ? (
        <section className="qa-claims" aria-label="关联依据">
          <div className="results-section-title"><span>依据与引句</span><span>{claims.length} 条结论</span></div>
          {claims.map((claim, index) => <article className="qa-claim" key={`${index}-${claim.text}`}><span className="qa-claim-index">{String(index + 1).padStart(2, "0")}</span><Fact claim={claim} onEvidence={onEvidence} /></article>)}
        </section>
      ) : <div className="results-notice">当前没有可核验的结论。请结合证据状态与任务提示阅读回答。</div>}
    </div>
  );
}

function ExtractResults({ result, onEvidence }: ResultsProps) {
  const structured = result.structured as ExtractResult;
  const elements = structured.elements ?? [];
  return (
    <div className="results-content">
      <Header title="政策要素" note="提取内容按维度整理；每项事实均可打开对应原文。" />
      {structured.documents?.length > 0 && <div className="results-document-strip">{structured.documents.map((document) => <div className="results-document-chip" key={document.planid}><FileText aria-hidden="true" size={14} /><span>{valueOrMissing(document.title)}</span><small>{document.planid || "未提供"}</small></div>)}</div>}
      {structured.dimensions?.length > 0 ? structured.dimensions.map((dimension) => {
        const rows = elements.filter((claim) => claim.dimension === dimension);
        const missing = structured.missing?.find((item) => item.dimension === dimension);
        return <section className="extract-section" key={dimension}><div className="extract-section-heading"><h2>{dimension}</h2><span>{rows.length ? `${rows.length} 项` : "待核实"}</span></div>{rows.length ? rows.map((claim, index) => <Fact key={`${dimension}-${index}-${claim.text}`} claim={claim} onEvidence={onEvidence} />) : <p className="extract-missing">{missing?.reason === "not_retrieved" ? "本轮未检索到相关正文。" : missing?.reason === "not_in_document" ? "本轮读取的原文未找到该项内容。" : "本轮未形成该项证据。"}</p>}</section>;
      }) : <AnswerFallback result={result} onEvidence={onEvidence} />}
    </div>
  );
}

function OrganizeResults({ result, onEvidence }: ResultsProps) {
  const structured = result.structured as OrganizeResult;
  const policies = structured.policies ?? [];
  const groups = structured.groups ?? [];
  return (
    <div className="results-content">
      <Header title="主题与文献" note={structured.scope?.topic ? `研究主题：${structured.scope.topic}` : "按主题浏览本轮检索到的政策文献。"} />
      {structured.coverage?.note && <div className="results-coverage"><Layers3 aria-hidden="true" size={15} /><span>{structured.coverage.note}</span></div>}
      {groups.length > 0 && <section className="organize-groups"><div className="results-section-title"><span>主题分组 <em className="analysis-label">分析</em></span><span>{groups.length} 个主题</span></div>{groups.map((group) => <div className="organize-group" key={group.topic}><div className="organize-group-name"><ChevronRight aria-hidden="true" size={15} />{group.topic}</div><div className="organize-group-policies">{group.planids.map((planid) => { const policy = policies.find((item) => item.planid === planid); return <span className="organize-group-tag" key={planid}>{policy?.title || planid}</span>; })}</div><div className="organize-group-claims">{group.claim_indexes.map((claimIndex) => { const claim = result.claims[claimIndex]; return claim ? <CitationButtons key={`${group.topic}-${claimIndex}`} claim={claim} onEvidence={onEvidence} compact /> : null; })}</div></div>)}</section>}
      {policies.length > 0 ? <section className="organize-policies"><div className="results-section-title"><span>相关文献</span><span>{policies.length} 篇候选</span></div>{policies.map((policy) => <PolicyCard key={policy.planid} policy={policy} claims={result.claims} onEvidence={onEvidence} />)}</section> : <AnswerFallback result={result} onEvidence={onEvidence} />}
    </div>
  );
}

function PolicyCard({ policy, claims, onEvidence }: { policy: OrganizePolicy; claims: Claim[]; onEvidence: (chunkId: string) => void }) {
  const identityOnly = policy.claim_indexes.length === 0;
  return (
    <article className="policy-card">
      <div className="policy-card-top"><div><span className="policy-card-status">{policy.read_status === "summarized" ? "已梳理" : policy.read_status === "not_retrieved" ? "未命中" : policy.read_status === "not_read" ? "未读取" : "未形成要点"}</span><h2>{valueOrMissing(policy.title)}</h2></div><span className="policy-planid">{valueOrMissing(policy.planid)}</span></div>
      <div className="policy-metadata"><span>{valueOrMissing(policy.area)}</span><i /> <span>{valueOrMissing(policy.fyp)}</span></div>
      {policy.topics.length > 0 && <div className="policy-topics"><em className="analysis-label">分析标签</em>{policy.topics.map((topic) => <span key={topic}>{topic}</span>)}</div>}
      {policy.relevance_note && <p className="policy-relevance"><span>分析</span>{policy.relevance_note}</p>}
      {!identityOnly && policy.claim_indexes.map((claimIndex) => { const claim = claims[claimIndex]; return claim ? <Fact key={`${policy.planid}-${claimIndex}`} claim={claim} onEvidence={onEvidence} /> : null; })}
      {identityOnly && <p className="policy-identity-note">本轮仅发现文献身份信息，尚无可展示的政策事实。</p>}
    </article>
  );
}

function ComparisonResults({ result, onEvidence, plans = false }: ResultsProps & { plans?: boolean }) {
  const structured = result.structured as ComparisonResult;
  const objects = structured.objects ?? [];
  const rows = structured.matrix ?? [];
  return (
    <div className="results-content results-content--compare">
      <Header title={plans ? "方案对照" : "政策比较矩阵"} note={plans ? "分析仅依赖下方已列出的支持条款，适用条件按原文呈现。" : undefined} />
      {structured.task_completion?.status === "partial" && (
        <div className="comparison-task-status" role="status">
          <strong>本轮仅完成部分比较</strong>
          <p>以下展示已取得依据的政策事实；未形成足够相关证据的维度不生成异同判断。
            {structured.task_completion.unsupported_dimensions.length > 0
              ? `待补充：${structured.task_completion.unsupported_dimensions.join("、")}`
              : ""}
          </p>
        </div>
      )}
      {structured.evaluation_criteria && structured.evaluation_criteria.length > 0 && <div className="comparison-criteria"><span>评价标准</span>{structured.evaluation_criteria.map((criterion) => <em key={criterion}>{criterion}</em>)}</div>}
      {rows.length > 0 && (
        <div className="comparison-scroll" role="region" aria-label="政策比较矩阵" tabIndex={0}>
          <table className="comparison-table" style={{ minWidth: `${170 + objects.length * 290 + 300}px` }}>
            <thead><tr>
              <th className="comparison-dimension-head">比较维度</th>
              {objects.map((object) => <th key={object.planid}>
                <div className="comparison-policy-heading">
                  <span className="comparison-policy-title">{valueOrMissing(object.title)}</span>
                  <ComparisonPolicySources planid={object.planid} rows={rows} claims={result.claims}
                    evidenceRefs={result.evidence_refs ?? []} onEvidence={onEvidence} />
                </div>
                <small className="comparison-policy-meta">
                  {valueOrMissing(object.area)} · {valueOrMissing(object.fyp)} · planid {valueOrMissing(object.planid)}
                </small>
              </th>)}
              <th className="comparison-analysis-head">差异分析</th>
            </tr></thead>
            <tbody>{rows.map((row) => <tr key={row.dimension}>
              <th className="comparison-dimension"><span>{row.dimension}</span></th>
              {objects.map((object) => {
                const cell = row.cells.find((item) => item.planid === object.planid);
                return <td key={`${row.dimension}-${object.planid}`}>
                  <ComparisonCellView cell={cell} claims={result.claims} onEvidence={onEvidence} />
                </td>;
              })}
              <td className="comparison-analysis-cell">
                <AnalysisView analysis={row.difference} cells={row.cells} claims={result.claims} />
              </td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
      {plans && structured.scheme_analysis?.map((scheme) => { const object = objects.find((item) => item.planid === scheme.planid); return <section className="scheme-analysis" key={scheme.planid}><div className="scheme-analysis-heading"><GitCompareArrows aria-hidden="true" size={17} /><div><span>方案分析</span><h2>{object?.title || scheme.planid}</h2></div></div><div className="scheme-analysis-grid"><AnalysisGroup title="优势特征" entries={scheme.advantages} cells={rows.flatMap((row) => row.cells.filter((cell) => cell.planid === scheme.planid))} claims={result.claims} onEvidence={onEvidence} /><AnalysisGroup title="限制" entries={scheme.limitations} cells={rows.flatMap((row) => row.cells.filter((cell) => cell.planid === scheme.planid))} claims={result.claims} onEvidence={onEvidence} /><AnalysisGroup title="适用条件" entries={scheme.applicability} cells={rows.flatMap((row) => row.cells.filter((cell) => cell.planid === scheme.planid))} claims={result.claims} onEvidence={onEvidence} /></div></section>; })}
      {structured.coverage_notes?.length > 0 && <div className="comparison-notes"><span>取证范围</span>{structured.coverage_notes.map((note, index) => <p key={`${index}-${note}`}>{note}</p>)}</div>}
      {rows.length === 0 && <AnswerFallback result={result} onEvidence={onEvidence} />}
    </div>
  );
}

function ComparisonCellView({ cell, claims, onEvidence }: { cell: ComparisonCell | undefined; claims: Claim[]; onEvidence: (chunkId: string) => void }) {
  if (!cell) return <span className="comparison-cell-empty">未提供</span>;
  if (cell.status !== "found") return <div className={`comparison-cell-state comparison-cell-state--${cell.status}`}><span>{cell.status === "not_retrieved" ? "未检索到" : "证据不足"}</span><p>{cell.reason || "本轮有限取证未能支持此项。"}</p></div>;
  const linked = claims.filter((claim) => cell.chunk_ids?.some((id) => claim.chunk_ids.includes(id)));
  return <div className="comparison-cell-facts">{linked.length ? linked.map((claim, index) => <Fact key={`${cell.cell_id}-${index}`} claim={claim} onEvidence={onEvidence} showCitations={false} />) : <span className="comparison-cell-empty">该单元格暂未关联可核验结论</span>}</div>;
}

function AnalysisView({ analysis, cells, claims }: { analysis: ComparisonAnalysis; cells: ComparisonCell[]; claims: Claim[] }) {
  const dependencies = cells.filter((cell) => analysis.supporting_cells.includes(cell.cell_id));
  const linkedClaims = claims.filter((claim) => dependencies.some((cell) => cell.chunk_ids?.some((id) => claim.chunk_ids.includes(id))));
  if (analysis.status === "insufficient") return <span className="comparison-cell-empty">依据不足，无法判断</span>;
  if (!analysis.text || !linkedClaims.length) return <span className="comparison-cell-empty">暂无具备原文关联的分析</span>;
  return <div className="comparison-analysis"><p>{analysis.text}</p></div>;
}

function AnalysisGroup({ title, entries, cells, claims }: { title: string; entries: ComparisonAnalysis[]; cells: ComparisonCell[]; claims: Claim[]; onEvidence: (chunkId: string) => void }) {
  return <div className="scheme-analysis-group"><h3>{title}</h3>{entries.length ? entries.map((entry, index) => <AnalysisView key={`${title}-${index}`} analysis={entry} cells={cells} claims={claims} />) : <p className="scheme-empty">暂无有依据的判断</p>}</div>;
}

function isIdentityFallback(structured: RunResult["structured"]): boolean {
  if (!structured || typeof structured !== "object" || "schema_version" in structured || "matrix" in structured || "elements" in structured) return false;
  return "documents" in structured && Array.isArray((structured as { documents?: unknown }).documents);
}

function IdentityFallback({ result }: { result: RunResult }) {
  const documents = (result.structured as { documents: { planid?: string; title?: string; area?: string | null; fyp?: string | null }[] }).documents;
  return <section className="identity-fallback"><Header title="本轮发现的文献" note="当前仅有来源身份信息，没有足够证据形成政策事实。" />{documents.map((document, index) => <article className="identity-document" key={`${document.planid}-${index}`}><FileText aria-hidden="true" size={16} /><div><h2>{valueOrMissing(document.title)}</h2><p>{valueOrMissing(document.area)} · {valueOrMissing(document.fyp)}</p></div><span>planid {valueOrMissing(document.planid)}</span></article>)}</section>;
}

export function Results({ result, onEvidence }: ResultsProps) {
  const structured = result.structured;
  if (isIdentityFallback(structured)) return <IdentityFallback result={result} />;
  if (!structured || Object.keys(structured).length === 0) return result.task_type === "qa" ? <QAResults result={result} onEvidence={onEvidence} /> : <AnswerFallback result={result} onEvidence={onEvidence} />;
  switch (result.task_type) {
    case "qa": return <QAResults result={result} onEvidence={onEvidence} />;
    case "extract": return <ExtractResults result={result} onEvidence={onEvidence} />;
    case "organize": return "schema_version" in structured && structured.schema_version === "organize/1" ? <OrganizeResults result={result} onEvidence={onEvidence} /> : <AnswerFallback result={result} onEvidence={onEvidence} />;
    case "compare": return "matrix" in structured && !("scheme_analysis" in structured) ? <ComparisonResults result={result} onEvidence={onEvidence} /> : <AnswerFallback result={result} onEvidence={onEvidence} />;
    case "compare_plans": return "matrix" in structured ? <ComparisonResults result={result} onEvidence={onEvidence} plans /> : <AnswerFallback result={result} onEvidence={onEvidence} />;
    default: return <AnswerFallback result={result} onEvidence={onEvidence} />;
  }
}

export interface EvidencePanelProps {
  result: RunResult;
  selectedChunkId: string | null;
  onClose: () => void;
}

export function EvidencePanel({ result, selectedChunkId, onClose }: EvidencePanelProps) {
  const reference: EvidenceRef | undefined = result.evidence_refs?.find((item) => item.chunk_id === selectedChunkId);
  const linkedClaims = selectedChunkId ? result.claims.filter((claim) => claim.chunk_ids.includes(selectedChunkId)) : [];
  const linkedQuotes = [...new Map(linkedClaims.flatMap((claim) => claim.quotes.map((quote) => [quote, claim] as const))).entries()]
    .map(([quote, claim]) => ({ quote, claim }));

  return (
    <aside className="evidence-panel" aria-label="证据详情">
      <div className="evidence-panel-heading"><div><span>来源核对</span><h2>证据详情</h2></div><button className="evidence-close" onClick={onClose} type="button" aria-label="关闭证据面板">×</button></div>
      {!selectedChunkId ? <div className="evidence-empty"><BookOpen aria-hidden="true" size={20} /><p>点击结果中的原文标记，在此查看来源与引句。</p></div> : reference ? <div className="evidence-panel-body">
        <div className="evidence-status-chip"><span />{result.evidence_status === "verified" ? "引文字面核验通过" : result.evidence_status === "insufficient" ? "证据不足" : "无可核验引文"}</div>
        <h3 className="evidence-title">{valueOrMissing(reference.title)}</h3>
        <dl className="evidence-metadata"><div><dt>地区</dt><dd>{valueOrMissing(reference.area)}</dd></div><div><dt>规划时期</dt><dd>{valueOrMissing(reference.fyp)}</dd></div><div><dt>planid</dt><dd>{valueOrMissing(reference.planid)}</dd></div><div><dt>政策类型</dt><dd>{valueOrMissing(reference.plantype_label)}</dd></div><div><dt>章节路径</dt><dd>{valueOrMissing(reference.section_path)}</dd></div></dl>
        {reference.quality_warnings.length > 0 && <section className="evidence-quality-warnings"><div className="evidence-section-title">来源质量提示</div>{reference.quality_warnings.map((warning, index) => <p key={`${index}-${warning}`}>{warning}</p>)}</section>}
        <section className="evidence-quotes"><div className="evidence-section-title">关联结论引句 <small>来源含本片段</small></div>{linkedQuotes.length ? linkedQuotes.map(({ claim, quote }, index) => <blockquote key={`${index}-${quote}`}><p>“{quote}”</p><footer>{claim.dimension || "政策原文"}</footer></blockquote>) : <p className="evidence-no-quote">本条来源暂未关联可展示的引句。</p>}</section>
        <p className="evidence-caveat">逐字核验只确认引句与来源文本一致，不保证引句在语义上足以支持结论。</p>
      </div> : <div className="evidence-empty"><p>本轮没有找到该来源的详细信息。</p></div>}
    </aside>
  );
}
