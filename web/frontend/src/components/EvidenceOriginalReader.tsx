import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, BookOpenText } from "lucide-react";
import { catalogClient } from "../api/catalogClient";
import type { EvidenceRef, PolicyContentResponse, PolicyDetailResponse, RunResult } from "../types";
import "./evidenceReader.css";

type Source = { reference: EvidenceRef; result: RunResult };

const display = (value: string | null | undefined) => value?.trim() || "未提供";

export function SourceHoverCard({ source, onOpen, onMouseEnter, onMouseLeave, style }: {
  source: Source;
  onOpen: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
  style: { top: number; left: number };
}) {
  const { reference, result } = source;
  const quotes = result.claims
    .filter(claim => claim.chunk_ids.includes(reference.chunk_id))
    .flatMap(claim => claim.quotes)
    .filter((quote, index, all) => all.indexOf(quote) === index)
    .slice(0, 2);
  return <aside className="source-hover-card" style={style} aria-label="原文预览"
    onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
    <span className="source-hover-eyebrow">来源预览</span>
    <strong>{display(reference.title)}</strong>
    <p className="source-hover-meta">{[reference.area, reference.fyp, reference.section_path].filter(Boolean).join(" · ")}</p>
    {quotes.length ? <div className="source-hover-excerpt"><small>已关联引句（非完整原文）</small>
      {quotes.map((quote, index) => <p key={index}>{quote}</p>)}
    </div> : <p className="source-hover-meta">本次引用未提供可展示的引句。</p>}
    <button type="button" onClick={onOpen}>打开政策原文 <ArrowRight size={14} aria-hidden="true" /></button>
  </aside>;
}

/** Standalone, read-only source view; retrieves the published original, never synthesizes it from quotations. */
export function EvidenceOriginalReader({ source, onBack }: { source: Source; onBack: () => void }) {
  const { reference } = source;
  const [detail, setDetail] = useState<PolicyDetailResponse | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [offsets, setOffsets] = useState([0]);
  const [cursor, setCursor] = useState(0);
  const [page, setPage] = useState<PolicyContentResponse | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const offset = offsets[cursor] ?? 0;

  useEffect(() => {
    const controller = new AbortController();
    setDetail(null);
    setDetailError(null);
    void catalogClient.detail(reference.planid, controller.signal).then(
      value => { if (!controller.signal.aborted) setDetail(value); },
      error => { if (!controller.signal.aborted) setDetailError(error instanceof Error ? error.message : "无法读取政策信息。"); },
    );
    return () => controller.abort();
  }, [reference.planid]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setPage(null);
    setPageError(null);
    void catalogClient.content(reference.planid, offset, 4000, controller.signal).then(
      value => { if (!controller.signal.aborted) setPage(value); },
      error => { if (!controller.signal.aborted) setPageError(error instanceof Error ? error.message : "无法读取政策原文。"); },
    ).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reference.planid, offset]);

  const navigate = (nextOffset: number) => {
    if (nextOffset < 0 || loading) return;
    const previous = offsets.indexOf(nextOffset);
    if (previous >= 0) setCursor(previous);
    else {
      setOffsets(current => [...current.slice(0, cursor + 1), nextOffset]);
      setCursor(cursor + 1);
    }
  };
  const codePointLength = page ? Array.from(page.text).length : 0;
  const versionChanged = reference.generation_id && detail &&
    reference.generation_id !== detail.dataset.generation_id;

  return <main className="source-reader" aria-label="政策原文阅读">
    <button type="button" className="source-reader-back" onClick={onBack}><ArrowLeft size={17} /> 返回研究对话</button>
    <article className="source-reader-document">
      <div className="source-reader-kicker"><BookOpenText size={17} /> 政策原文</div>
      <h1>{display(detail?.policy.title ?? reference.title)}</h1>
      <p className="source-reader-meta">{[reference.area, reference.fyp, reference.plantype_label].filter(Boolean).join(" · ")}</p>
      <p className="source-reader-meta">planid：{reference.planid}{reference.section_path ? ` · 引用章节：${reference.section_path}` : ""}</p>
      {versionChanged && <p className="source-reader-warning" role="alert">当前发布原文版本与生成引用时的版本不同，请核对文献内容后使用。</p>}
      {detailError && <p className="source-reader-warning" role="alert">{detailError}</p>}
      {detail?.policy.sections && detail.policy.sections.length > 0 && <details className="source-reader-sections">
        <summary>章节目录（{detail.policy.sections.length}）</summary>
        <div>{detail.policy.sections.map(section => <button type="button" key={section.index}
          onClick={() => navigate(section.start)}>{section.path}</button>)}</div>
      </details>}
      <section className="source-reader-body" aria-label="政策正文">
        <div className="source-reader-body-heading"><strong>正文</strong><span>{page ? `${page.offset}–${page.offset + codePointLength} / ${page.total_chars} 字` : "按页读取"}</span></div>
        {loading && <p role="status">正在读取原文…</p>}
        {pageError && <p className="source-reader-warning" role="alert">{pageError}</p>}
        {page && <pre>{page.text}</pre>}
        {page && <div className="source-reader-pagination">
          <button type="button" disabled={loading || cursor === 0} onClick={() => setCursor(current => current - 1)}>上一页</button>
          <span>第 {cursor + 1} 页</span>
          <button type="button" disabled={loading || !page.has_more || codePointLength === 0}
            onClick={() => navigate(page.offset + codePointLength)}>下一页 <ArrowRight size={14} /></button>
        </div>}
      </section>
    </article>
  </main>;
}
