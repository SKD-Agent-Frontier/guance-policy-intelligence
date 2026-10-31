import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Search, X } from "lucide-react";
import { catalogClient } from "../api/catalogClient";
import { isRealMode } from "../api/client";
import { ApiError } from "../api/http";
import { extractPolicyVersionPin } from "../api/policyFacets";
import { PolicyFacetsPanel } from "./PolicyFacetsPanel";
import { usePolicyCatalogFacets } from "../hooks/usePolicyCatalogFacets";
import type {
  PolicyContentResponse,
  PolicyDetailResponse,
  PolicyFacetFilters,
  PolicyListResponse,
  PolicyListParams,
} from "../types";
import "../catalog.css";

type CatalogParams = PolicyListParams & { page: number; page_size: number };

type Filters = Omit<CatalogParams, "page" | "page_size">;

const PAGE_SIZE = 20;
const CONTENT_PAGE_SIZE = 4000;
const KNOWN_PERIODS: Record<string, string> = {
  "十一五": "十一五",
  "第十一个五年规划": "十一五",
  "第十一个五年计划": "十一五",
  "十二五": "十二五",
  "第十二个五年规划": "十二五",
  "第十二个五年计划": "十二五",
  "十三五": "十三五",
  "第十三个五年规划": "十三五",
  "第十三个五年计划": "十三五",
  "十四五": "十四五",
  "第十四个五年规划": "十四五",
  "第十四个五年计划": "十四五",
  "十五五": "十五五",
  "第十五个五年规划": "十五五",
  "第十五个五年计划": "十五五",
  "十五": "十五",
  "第十个五年规划": "十五",
  "第十个五年计划": "十五",
};

function metadata(value: string | null | undefined) {
  return value?.trim() || "未提供";
}

function formatPeriod(fyp: string | null | undefined, year: number | null | undefined) {
  const normalized = fyp?.trim();
  if (normalized && KNOWN_PERIODS[normalized]) return KNOWN_PERIODS[normalized];
  if (normalized && normalized !== "政府工作报告") return normalized;
  return typeof year === "number" && Number.isInteger(year) && year >= 1 && year <= 9999
    ? `${year}年`
    : "未提供";
}

function highlightLiteral(text: string, query: string | null | undefined): React.ReactNode {
  if (!query) return text;

  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let matchStart = text.indexOf(query, cursor);
  while (matchStart !== -1) {
    if (matchStart > cursor) parts.push(text.slice(cursor, matchStart));
    const matchEnd = matchStart + query.length;
    parts.push(<mark className="catalog-highlight" key={`${matchStart}-${matchEnd}`}>{text.slice(matchStart, matchEnd)}</mark>);
    cursor = matchEnd;
    matchStart = text.indexOf(query, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

function sameVersion(left: PolicyListResponse["dataset"], right: PolicyListResponse["dataset"]) {
  return left.generation_id === right.generation_id && left.revision === right.revision;
}

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message;
  return "文献目录暂时无法访问，请重试。";
}

function makeParams(filters: Filters, page: number, pageSize: number): CatalogParams {
  const normalized: Record<string, string | number> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (typeof value === "string") {
      const clean = value.trim();
      if (clean) normalized[key] = clean;
    } else if (typeof value === "number") {
      normalized[key] = value;
    }
  }
  return { ...normalized, page, page_size: pageSize } as CatalogParams;
}

const NEW_FILTER_KEYS: (keyof PolicyFacetFilters)[] = [
  "admin_level", "province_code", "prefecture_code", "county_code", "region_status", "year", "fyp_status", "plantype_status",
];

function hasNewFilters(filters: PolicyFacetFilters) {
  return NEW_FILTER_KEYS.some(key => filters[key] !== undefined);
}

export function PolicyCatalog() {
  const [filters, setFilters] = useState<Filters>({ q: "", area: "", fyp: "", plantype: "" });
  const [appliedFilters, setAppliedFilters] = useState<Filters>({});
  const appliedFiltersRef = useRef<Filters>({});
  const { facets, regions, status: facetsStatus, errorMessage: facetsError, refresh: refreshFacets, reload: reloadFacets } = usePolicyCatalogFacets();
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [list, setList] = useState<PolicyListResponse | null>(null);
  const [dataset, setDataset] = useState<PolicyListResponse["dataset"] | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [reading, setReading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [detail, setDetail] = useState<PolicyDetailResponse | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [content, setContent] = useState<PolicyContentResponse | null>(null);
  const [contentLoading, setContentLoading] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [pageOffsets, setPageOffsets] = useState([0]);
  const [pageCursor, setPageCursor] = useState(0);
  const listController = useRef<AbortController | null>(null);
  const detailController = useRef<AbortController | null>(null);
  const contentController = useRef<AbortController | null>(null);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const contentSequence = useRef(0);
  const selectedPlanRef = useRef<string | null>(null);
  const applySequence = useRef(0);
  const expectedFacetDataset = useRef<PolicyListResponse["dataset"] | null>(null);
  const appliedParams = useRef<CatalogParams>({ page: 1, page_size: PAGE_SIZE });
  const previewTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const returnPlanId = useRef<string | null>(null);

  const loadList = useCallback(async (params: CatalogParams, showResults = true) => {
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    const sequence = ++listSequence.current;
    appliedParams.current = params;
    setHasSearched(showResults);
    setListLoading(true);
    setListError(null);
    try {
      const response = await catalogClient.list(params, controller.signal);
      if (sequence !== listSequence.current) return;
      const expected = expectedFacetDataset.current;
      if (expected) {
        const actualPin = extractPolicyVersionPin(response.dataset);
        const expectedPin = extractPolicyVersionPin(expected);
        if (!actualPin || !expectedPin || JSON.stringify(actualPin) !== JSON.stringify(expectedPin)
          || response.dataset.total_documents !== expected.total_documents || response.dataset.partial !== expected.partial) {
          setList(null);
          setListError("目录结果与维度筛选使用的数据版本不一致，请重新加载筛选项后再检索。");
          return;
        }
      }
      setDataset(response.dataset);
      setList(showResults ? response : null);
    } catch (error) {
      if (sequence !== listSequence.current || controller.signal.aborted) return;
      setList(null);
      if (error instanceof ApiError && error.status === 409) {
        void reloadFacets(appliedFiltersRef.current);
        setListError("目录数据版本已更新，已重新加载筛选项。请重新检索。");
      } else {
        setListError(errorMessage(error));
      }
    } finally {
      if (sequence === listSequence.current) setListLoading(false);
    }
  }, [reloadFacets]);

  const loadContent = useCallback(async (planid: string, offset: number) => {
    contentController.current?.abort();
    const controller = new AbortController();
    contentController.current = controller;
    const sequence = ++contentSequence.current;
    setContentLoading(true);
    setContentError(null);
    setContent(null);
    try {
      const response = await catalogClient.content(planid, offset, CONTENT_PAGE_SIZE, controller.signal);
      if (sequence !== contentSequence.current || selectedPlanRef.current !== planid) return;
      setContent(response);
    } catch (error) {
      if (sequence !== contentSequence.current || controller.signal.aborted) return;
      setContentError(errorMessage(error));
    } finally {
      if (sequence === contentSequence.current) setContentLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (planid: string) => {
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    const sequence = ++detailSequence.current;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    try {
      const response = await catalogClient.detail(planid, controller.signal);
      if (sequence !== detailSequence.current || selectedPlanRef.current !== planid) return;
      setDetail(response);
    } catch (error) {
      if (sequence !== detailSequence.current || controller.signal.aborted) return;
      setDetailError(errorMessage(error));
    } finally {
      if (sequence === detailSequence.current) setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    const initialApply = applySequence.current;
    void refreshFacets({}).then(outcome => {
      if (!active || initialApply !== applySequence.current) return;
      if (outcome.pin && (outcome.status === "ready" || outcome.status === "unavailable") && outcome.dataset) {
        expectedFacetDataset.current = outcome.dataset;
        void loadList({ ...outcome.pin, page: 1, page_size: 1 }, false);
      } else if (outcome.status === "unavailable") {
        expectedFacetDataset.current = null;
        void loadList({ page: 1, page_size: 1 }, false);
      } else if (!outcome.errorMessage?.includes("版本")) {
        expectedFacetDataset.current = null;
        void loadList({ page: 1, page_size: 1 }, false);
      } else {
        setListError("维度筛选版本暂不可用，请重新加载筛选项。");
      }
    });
    return () => {
      active = false;
      applySequence.current += 1;
      listSequence.current += 1;
      listController.current?.abort();
      detailSequence.current += 1;
      detailController.current?.abort();
      contentSequence.current += 1;
      contentController.current?.abort();
      if (previewTimer.current) clearTimeout(previewTimer.current);
    };
  }, [loadList, refreshFacets]);

  const submitSearch = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void applyFilters(filters);
  };

  const changePage = (page: number) => {
    if (listLoading || !list || page < 1 || page > list.total_pages) return;
    closePolicy();
    void loadList({ ...appliedParams.current, page });
  };

  const cancelPreviewTimer = () => {
    if (previewTimer.current) clearTimeout(previewTimer.current);
  };

  const openPolicy = (planid: string, asReader = true) => {
    cancelPreviewTimer();
    if (asReader) returnPlanId.current = planid;
    setReading(asReader);
    if (selectedPlanRef.current === planid) {
      if (asReader && (pageOffsets[pageCursor] ?? 0) !== 0) {
        setPageOffsets([0]);
        setPageCursor(0);
        void loadContent(planid, 0);
      }
      return;
    }
    selectedPlanRef.current = planid;
    setSelectedPlanId(planid);
    setPageOffsets([0]);
    setPageCursor(0);
    setContent(null);
    setContentError(null);
    void loadDetail(planid);
    void loadContent(planid, 0);
  };

  const closePolicy = () => {
    cancelPreviewTimer();
    setReading(false);
    selectedPlanRef.current = null;
    setSelectedPlanId(null);
    setDetail(null);
    setContent(null);
    setDetailError(null);
    setContentError(null);
    detailSequence.current += 1;
    detailController.current?.abort();
    contentSequence.current += 1;
    contentController.current?.abort();
  };

  const applyFilters = useCallback(async (nextFilters: Filters, showResults = true) => {
    const currentApply = ++applySequence.current;
    listSequence.current += 1;
    listController.current?.abort();
    setList(null);
    setListLoading(true);
    closePolicy();
    setFilters(nextFilters);
    const normalized = makeParams(nextFilters, 1, pageSize);
    const { page: _page, page_size: _pageSize, ...cleanFilters } = normalized;
    setAppliedFilters(cleanFilters);
    appliedFiltersRef.current = cleanFilters;
    const outcome = await refreshFacets(cleanFilters);
    if (currentApply !== applySequence.current) return;
    if (outcome.pin && (outcome.status === "ready" || (outcome.status === "unavailable" && !hasNewFilters(nextFilters))) && outcome.dataset) {
      expectedFacetDataset.current = outcome.dataset;
      void loadList({ ...normalized, ...outcome.pin }, showResults);
      return;
    }
    expectedFacetDataset.current = null;
    if (outcome.status !== "unavailable" || hasNewFilters(nextFilters)) {
      setListLoading(false);
      setList(null);
      setListError("维度筛选或版本校验暂不可用，当前条件尚无可用结果；请重新加载筛选项。");
      return;
    }
    void loadList(normalized, showResults);
  }, [loadList, pageSize, refreshFacets]);

  const reloadAppliedFilters = async () => {
    const currentApply = ++applySequence.current;
    listSequence.current += 1;
    listController.current?.abort();
    setList(null);
    setListLoading(true);
    const outcome = await reloadFacets(appliedFiltersRef.current);
    if (currentApply !== applySequence.current) return;
    if (outcome.status === "ready" && outcome.pin && outcome.dataset) {
      expectedFacetDataset.current = outcome.dataset;
      void loadList({ ...makeParams(appliedFiltersRef.current, 1, pageSize), ...outcome.pin }, hasSearched);
    } else if (outcome.status === "unavailable" && !hasNewFilters(appliedFiltersRef.current)) {
      expectedFacetDataset.current = null;
      void loadList(makeParams(appliedFiltersRef.current, 1, pageSize), hasSearched);
    } else {
      setListLoading(false);
      setListError("无法重新加载筛选版本，请稍后重试。");
    }
  };

  const previewPolicy = (planid: string) => {
    if (reading) return;
    cancelPreviewTimer();
    previewTimer.current = setTimeout(() => openPolicy(planid, false), 250);
  };

  const dismissPreview = () => {
    if (reading) return;
    cancelPreviewTimer();
    previewTimer.current = setTimeout(closePolicy, 250);
  };

  useEffect(() => {
    if (reading || !selectedPlanId) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePolicy();
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  });

  useEffect(() => {
    if (reading) {
      mainRef.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0 });
    }
    else if (returnPlanId.current) {
      const cards = mainRef.current?.querySelectorAll<HTMLButtonElement>(".catalog-policy-card");
      Array.from(cards ?? []).find((card) => card.dataset.planid === returnPlanId.current)?.focus();
      returnPlanId.current = null;
    }
  }, [reading, content?.offset]);

  const retryDetail = () => {
    if (selectedPlanId) {
      void loadDetail(selectedPlanId);
      void loadContent(selectedPlanId, pageOffsets[pageCursor] ?? 0);
    }
  };

  const changeContentPage = (direction: -1 | 1) => {
    if (!content || contentLoading || !selectedPlanId) return;
    if (direction < 0) {
      const previousCursor = pageCursor - 1;
      const offset = pageOffsets[previousCursor];
      if (offset === undefined) return;
      setPageCursor(previousCursor);
      void loadContent(selectedPlanId, offset);
      return;
    }
    const codePointLength = Array.from(content.text).length;
    const nextOffset = content.offset + codePointLength;
    if (!content.has_more || codePointLength === 0 || nextOffset <= content.offset) return;
    const nextCursor = pageCursor + 1;
    setPageOffsets((offsets) => [...offsets.slice(0, nextCursor), nextOffset]);
    setPageCursor(nextCursor);
    void loadContent(selectedPlanId, nextOffset);
  };

  const jumpToSection = (offset: number) => {
    if (!selectedPlanId) return;
    setPageOffsets([offset]);
    setPageCursor(0);
    void loadContent(selectedPlanId, offset);
  };

  const policy = detail?.policy;
  const DetailContainer = reading ? "article" : "aside";
  const matchedLabel = list?.query.q
    ? "字面匹配"
    : "符合当前筛选";
  const regionLabel = (code: string) => {
    const visit = (nodes: NonNullable<typeof regions>["data"]): string | null => {
      for (const node of nodes) {
        if (node.code === code) return node.name;
        const child = visit(node.children);
        if (child) return child;
      }
      return null;
    };
    return visit(regions?.data ?? []) ?? "地区";
  };
  const filterLabels: Record<string, string> = {
    q: "关键词", area: "地区精确值", fyp: "规划期", plantype: "政策类型",
    admin_level: "行政级别", province_code: "省级地区", prefecture_code: "地级地区", county_code: "县级地区",
    region_status: "地区状态", year: "年份", fyp_status: "规划期状态", plantype_status: "类型状态",
  };
  const filterValueLabel = (key: string, value: unknown) => {
    if (key === "admin_level") return ({ national: "国家级", province: "省级", prefecture: "市级", county: "区县级", unclassified: "未分类" } as Record<string, string>)[String(value)] ?? String(value);
    if (["province_code", "prefecture_code", "county_code"].includes(key)) return regionLabel(String(value));
    if (key === "region_status") return ({ mapped: "已映射", unclassified: "未分类", not_applicable: "不适用" } as Record<string, string>)[String(value)] ?? String(value);
    if (key === "fyp_status") return "未提供";
    if (key === "plantype_status") return "未提供";
    if (key === "year" && value === "unknown") return "未知";
    return String(value);
  };
  const appliedEntries = Object.entries(appliedFilters).filter(([, value]) => value !== undefined && value !== "");
  const facetFilterChange = (patch: Partial<PolicyFacetFilters>) => {
    void applyFilters({ ...filters, ...patch });
  };
  const removeAppliedFilter = (key: string) => {
    const next = { ...appliedFilters } as Filters;
    delete next[key as keyof Filters];
    if (key === "fyp") delete next.fyp_status;
    if (key === "plantype") delete next.plantype_status;
    if (key === "province_code") { delete next.prefecture_code; delete next.county_code; }
    if (key === "prefecture_code") delete next.county_code;
    void applyFilters(next);
  };

  return (
    <main ref={mainRef} tabIndex={-1} className={reading ? "catalog-page policy-reader" : "catalog-page"} aria-label={reading ? "政策原文阅读" : "政策文献目录"}>
      {!reading && <>
      <header className="catalog-header">
        <div>
          <h1>政策文献目录</h1>
        </div>
        {(!isRealMode || dataset?.source === "demo_synthetic") && <span className="catalog-demo-badge">演示目录数据</span>}
      </header>

      <PolicyFacetsPanel
        filters={filters}
        facets={facets}
        regions={regions}
        status={facetsStatus}
        errorMessage={facetsError}
        onChange={facetFilterChange}
        onRetry={() => void reloadAppliedFilters()}
      />

      <form className="catalog-search" onSubmit={submitSearch}>
        <label className="catalog-query-field">
          <span>关键词字面检索</span>
          <span className="catalog-query-input">
            <Search size={17} aria-hidden="true" />
            <input
              type="search"
              value={filters.q ?? ""}
              onChange={(event) => setFilters((current) => ({ ...current, q: event.target.value }))}
              placeholder="输入政策标题或原文中的词语"
            />
          </span>
        </label>
        <label>
          <span>地区精确筛选</span>
          <input value={filters.area ?? ""} onChange={(event) => setFilters((current) => ({ ...current, area: event.target.value }))} placeholder="例如：北京市" />
        </label>
        <label>
          <span>规划期精确筛选</span>
          <input value={filters.fyp ?? ""} onChange={(event) => setFilters((current) => ({ ...current, fyp: event.target.value, fyp_status: undefined }))} placeholder="例如：十四五" />
        </label>
        <label>
          <span>政策类型精确筛选</span>
          <input value={filters.plantype ?? ""} onChange={(event) => setFilters((current) => ({ ...current, plantype: event.target.value, plantype_status: undefined }))} placeholder="输入完整类型名称" />
        </label>
        <label className="catalog-page-size">
          <span>每页条数</span>
          <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
            {[10, 20, 50, 100].map((size) => <option key={size} value={size}>{size} 篇</option>)}
          </select>
        </label>
        <button className="catalog-search-button" type="submit">
          <Search size={15} /> 检索目录
        </button>
      </form>

      {appliedEntries.length > 0 && <div className="catalog-applied-filters" aria-label="已应用筛选条件">
        <span>已应用：</span>
        {appliedEntries.map(([key, value]) => <button type="button" key={key} onClick={() => removeAppliedFilter(key)} aria-label={`移除${filterLabels[key] ?? "筛选"} ${filterValueLabel(key, value)}`}>
          {filterLabels[key] ?? "筛选"}：{filterValueLabel(key, value)}<X size={13} aria-hidden="true" />
        </button>)}
        <button type="button" onClick={() => void applyFilters({})}>清除全部</button>
      </div>}

      {dataset && <p className="catalog-library-count">当前库共有 {dataset.total_documents} 篇政策文献</p>}

      <div className="catalog-layout">
        <section className="catalog-results" aria-label="政策列表" aria-busy={listLoading}>
          {hasSearched && <div className="catalog-results-heading">
            <div>
              <strong>{list ? `${matchedLabel} ${list.matched_documents} 篇` : "政策列表"}</strong>
              {list?.query.q && <small>字面匹配结果不等于全部语义相关政策。</small>}
            </div>
            {list && <span className="catalog-page-count">第 {list.page} / {Math.max(list.total_pages, 1)} 页</span>}
          </div>}

          {listLoading && <p className="catalog-status" role="status">正在读取当前发布目录…</p>}
          {listError && (
            <div className="catalog-error" role="alert">
              <p>{listError}</p>
              <button type="button" onClick={() => void reloadAppliedFilters()}>重试</button>
            </div>
          )}
          {!listLoading && !listError && list?.items.length === 0 && (
            <p className="catalog-empty">当前条件下没有匹配的政策。</p>
          )}
          {!listLoading && !listError && list && list.items.length > 0 && (
            <div className="catalog-policy-list">
              {list.items.map((item) => (
                <button
                  type="button"
                  className={`catalog-policy-card ${selectedPlanId === item.planid ? "is-selected" : ""}`}
                  key={item.planid}
                  data-planid={item.planid}
                  onClick={() => openPolicy(item.planid)}
                  onMouseEnter={() => {
                    if (!window.matchMedia?.("(hover: none)").matches) previewPolicy(item.planid);
                  }}
                  onMouseLeave={dismissPreview}
                  onFocus={() => previewPolicy(item.planid)}
                  onBlur={dismissPreview}
                >
                  <span className="catalog-policy-title">{highlightLiteral(item.title?.trim() ? item.title : "未提供", list.query.q)}</span>
                  <span className="catalog-policy-meta">
                    <span>{metadata(item.area)}</span><span>{formatPeriod(item.fyp, item.year)}</span><span>{metadata(item.plantype_label)}</span>
                  </span>
                  <span className="catalog-policy-id">planid：{item.planid}</span>
                  {item.matches.map((match, index) => <span className="catalog-snippet" key={`${match.field}-${match.offset}-${index}`}>{highlightLiteral(match.snippet, list.query.q)}</span>)}
                </button>
              ))}
            </div>
          )}

          {list && list.total_pages > 1 && (
            <nav className="catalog-pagination" aria-label="目录分页">
              <button type="button" onClick={() => changePage(list.page - 1)} disabled={listLoading || list.page <= 1}>
                <ArrowLeft size={15} /> 上一页
              </button>
              <span>{list.page} / {list.total_pages}</span>
              <button type="button" onClick={() => changePage(list.page + 1)} disabled={listLoading || list.page >= list.total_pages}>
                下一页 <ArrowRight size={15} />
              </button>
            </nav>
          )}
        </section>
      </div>
      </>}

      {reading && <button type="button" className="catalog-reader-back" onClick={closePolicy}><ArrowLeft size={17} />返回检索结果</button>}

        {selectedPlanId && (
          <DetailContainer className={`catalog-detail ${reading ? "catalog-reader-document" : "catalog-preview"}`} aria-label={reading ? "政策原文正文" : "政策详情预览"} aria-busy={detailLoading || contentLoading}
            onMouseEnter={cancelPreviewTimer} onMouseLeave={dismissPreview} onFocusCapture={cancelPreviewTimer}
            onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) dismissPreview(); }}>
            {!reading && <div className="catalog-detail-topline">
              <span>政策详情预览</span>
              <button type="button" className="catalog-close" aria-label="关闭政策预览" onClick={closePolicy}><X size={18} /></button>
            </div>}
            {detailLoading && <p className="catalog-status" role="status">正在读取政策信息…</p>}
            {detailError && (
              <div className="catalog-error" role="alert">
                <p>{detailError}</p><button type="button" onClick={retryDetail}>重试</button>
              </div>
            )}
            {policy && (
              <>
                {reading ? <h1>{metadata(policy.title)}</h1> : <h2>{metadata(policy.title)}</h2>}
                {list && detail && !sameVersion(list.dataset, detail.dataset) && (
                  <p className="catalog-version-note">政策信息与检索时的数据版本不同，请返回目录重新检索后核对。</p>
                )}
                {!reading && <>
                <dl className="catalog-metadata">
                  <div><dt>地区</dt><dd>{metadata(policy.area)}</dd></div>
                  <div><dt>规划期</dt><dd>{formatPeriod(policy.fyp, policy.year)}</dd></div>
                  <div><dt>政策类型</dt><dd>{metadata(policy.plantype_label)}</dd></div>
                  <div><dt>planid</dt><dd>{policy.planid}</dd></div>
                </dl>
                <section className="catalog-sections" aria-label="章节目录">
                  <h3>章节目录</h3>
                  {policy.sections.length > 0 ? (
                    <ol>
                      {policy.sections.map((section) => (
                        <li key={section.index}>
                          <button type="button" onClick={() => jumpToSection(section.start)} title={`跳转到${section.path}`}>
                            {section.path}
                          </button>
                        </li>
                      ))}
                    </ol>
                  ) : <p>该政策没有可用的章节目录。</p>}
                </section>
                </>}
                <section className="catalog-original" aria-label="政策原文">
                  <div className="catalog-original-heading"><h3>政策原文</h3><span>{content ? `${content.offset} / ${content.total_chars} 字` : "按页读取"}</span></div>
                  {contentLoading && <p className="catalog-status" role="status">正在读取原文…</p>}
                  {contentError && (
                    <div className="catalog-error" role="alert"><p>{contentError}</p><button type="button" onClick={() => void loadContent(selectedPlanId, pageOffsets[pageCursor] ?? 0)}>重试</button></div>
                  )}
                  {content && <pre className="catalog-original-text">{content.text}</pre>}
                  {content && list && !sameVersion(list.dataset, content.dataset) && (
                    <p className="catalog-version-note">原文与检索时的数据版本不同，请返回目录重新检索后核对。</p>
                  )}
                  {content && (
                    <div className="catalog-content-pagination">
                      <button type="button" onClick={() => changeContentPage(-1)} disabled={contentLoading || pageCursor === 0}>
                        <ArrowLeft size={14} /> 上一页
                      </button>
                      <span>{content.offset}–{content.offset + Array.from(content.text).length} / {content.total_chars}</span>
                      <button type="button" onClick={() => changeContentPage(1)} disabled={contentLoading || !content.has_more || content.text.length === 0}>
                        下一页 <ArrowRight size={14} />
                      </button>
                    </div>
                  )}
                </section>
                {!reading && <button type="button" className="catalog-preview-read" onClick={() => openPolicy(selectedPlanId)}>打开原文 <ArrowRight size={15} /></button>}
              </>
            )}
          </DetailContainer>
        )}
    </main>
  );
}
