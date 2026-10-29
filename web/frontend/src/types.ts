export type TaskType = "qa" | "extract" | "organize" | "compare" | "compare_plans";

export interface ResearchRequest {
  question: string;
  task_type: TaskType;
  targets?: string[];
  dimensions?: string[];
}

export type RunStatus = "completed" | "failed" | "limit_reached";
export type EvidenceStatus = "verified" | "insufficient" | "none";

export interface RunError {
  code: string;
  message: string;
}

export interface Claim {
  text: string;
  chunk_ids: string[];
  quotes: string[];
  dimension: string | null;
  anchors: string[];
}

export interface SourceMeta {
  document_id: string | null;
  document_version_id: string | null;
  generation_id: string | null;
  source_hash: string | null;
  section_id: string | null;
  section_char_start: number | null;
  section_char_end: number | null;
  offset_basis: string | null;
  offset_unit: string | null;
  body_origin: number | null;
  contract_version: string | null;
  quality_warnings: string[];
}

export interface EvidenceRef extends SourceMeta {
  planid: string;
  chunk_id: string;
  parent_id: string | null;
  title: string;
  area: string | null;
  fyp: string | null;
  admin_level: string | null;
  plantype_label: string | null;
  section_idx: number | null;
  section_path: string;
  char_start: number;
  char_end: number;
  chunk_char_start: number | null;
  chunk_char_end: number | null;
  truncated: boolean;
  source: "kb_search" | "kb_read";
  observed_ranges: [number, number][];
  complete: boolean;
  out_of_section: boolean;
}

export interface ToolCall {
  name: string;
  status: string;
  message: string;
  elapsed_ms: number;
}

export interface RunMeta {
  elapsed_ms?: number;
  tool_calls?: ToolCall[];
  [key: string]: unknown;
}

export interface Route {
  kind?: string;
  planids?: string[];
  titles?: string[];
  areas?: string[];
  fyps?: string[];
  sub_query?: string;
  targets?: string[] | null;
  [key: string]: unknown;
}

export interface ExtractDocument {
  planid: string;
  title: string;
  area: string | null;
  fyp: string | null;
}

export interface ExtractMissingDimension {
  dimension: string;
  reason: "not_in_document" | "not_retrieved";
}

export interface ExtractResult {
  dimensions: string[];
  documents: ExtractDocument[];
  elements: Claim[];
  missing: ExtractMissingDimension[];
}

export interface DocumentIdentityFallback {
  documents: {
    planid: string;
    title: string | null;
    area: string | null;
    fyp: string | null;
    admin_level?: string | null;
    plantype_label?: string | null;
    chunk_ids?: string[];
  }[];
  verification: { policy_facts: "unverified"; reason?: string };
}

export type OrganizeReadStatus =
  | "summarized"
  | "read_no_valid_points"
  | "irrelevant"
  | "no_text"
  | "read_failed"
  | "not_read"
  | "not_retrieved";

export type OrganizeRelevance = "high" | "medium" | "low" | "irrelevant";

export interface OrganizeQuery {
  query: string;
  status: string;
  hits: number;
  new_documents: number;
}

export interface OrganizeScope {
  topic: string;
  areas: string[];
  fyps: string[];
  plantypes: string[];
  categories: string[];
  dimensions: string[];
  targets: string[] | null;
  effective: {
    queries: OrganizeQuery[];
    area: string | null;
    fyp: string | null;
    top_k: number;
  };
  post_filters: {
    condition: string;
    values: string[];
    excluded_unknown: number;
    excluded_mismatch: number;
    note: string;
  }[];
  unapplied: { condition: string; reason: string }[];
}

export interface OrganizePolicy {
  planid: string;
  title: string | null;
  area: string | null;
  fyp: string | null;
  admin_level: string | null;
  plantype_label: string | null;
  read_status: OrganizeReadStatus;
  relevance: OrganizeRelevance | null;
  relevance_note: string;
  topics: string[];
  summary: string | null;
  claim_indexes: number[];
  chunk_ids_hit: string[];
  chunks_read: number;
  full_text_covered: boolean;
  read_attempts: { chunk_id: string; status: string }[];
}

export interface OrganizeResult {
  schema_version: "organize/1";
  scope: OrganizeScope;
  coverage: {
    documents_found: number;
    documents_after_filter: number;
    documents_read: number;
    documents_summarized: number;
    chunks_read: number;
    limits: string[];
    note: string;
  };
  policies: OrganizePolicy[];
  groups: { topic: string; planids: string[]; claim_indexes: number[] }[];
  verification: {
    policy_facts: "claims_only";
    analysis_fields: string[];
    note: string;
  };
}

export interface ComparisonObject {
  planid: string;
  title: string;
  area: string | null;
  fyp: string | null;
  plantype_label: string | null;
}

export interface ComparisonCell {
  cell_id: string;
  planid: string;
  status: "found" | "not_retrieved" | "insufficient";
  text?: string;
  chunk_ids?: string[];
  quotes?: string[];
  source_contexts?: string[];
  reason?: string;
}

export interface ComparisonAnalysis {
  text: string;
  supporting_cells: string[];
  kind: "analysis";
  scope?: "supporting_cells_only";
  status?: "supported" | "insufficient";
  derivation?: "extractive_conditions" | "extractive_support_features";
}

export interface ComparisonResult {
  objects: ComparisonObject[];
  dimensions: string[];
  matrix: {
    dimension: string;
    cells: ComparisonCell[];
    difference: ComparisonAnalysis;
    comparability: { comparable: boolean; reason: string };
  }[];
  comparability: { comparable: boolean; reason: string };
  coverage_notes: string[];
  analysis_gaps: { field: string; reason: string; message: string; dimension?: string; planid?: string }[];
  scheme_analysis?: {
    planid: string;
    advantages: ComparisonAnalysis[];
    limitations: ComparisonAnalysis[];
    applicability: ComparisonAnalysis[];
  }[];
  evaluation_criteria?: string[];
}

export type StructuredResult =
  | OrganizeResult
  | ComparisonResult
  | ExtractResult
  | DocumentIdentityFallback
  | Record<string, never>
  | null;

export interface RunResult {
  run_id: string;
  session_id: string;
  status: RunStatus;
  evidence_status: EvidenceStatus;
  answer: string;
  claims: Claim[];
  evidence_refs: EvidenceRef[];
  dropped_chunk_ids: string[];
  error: RunError | null;
  meta: RunMeta;
  warnings: string[];
  task_type: TaskType;
  structured: StructuredResult;
  route: Route | null;
}
