export type TaskType = "qa" | "extract" | "organize" | "compare" | "compare_plans";

export interface ResearchRequest {
  question: string;
  task_type: TaskType;
  targets?: string[] | null;
  dimensions?: string[] | null;
  session_id?: string | null;
}

export type PreparationStatus = "needs_clarification" | "ready" | "blocked";

export interface PreparationToken {
  state_id: string;
  revision: number;
}

export interface ResearchPreparationRequest {
  mode: TaskType;
  message: string;
  state?: PreparationToken;
}

export interface PreparationOption {
  id: string;
  question: string;
  choices: string[];
}

export interface TimeScope {
  start: string | null;
  end: string | null;
  precision: "exact" | "year" | "range" | "unspecified";
  timezone: string | null;
}

export interface Condition {
  id: string;
  text: string;
}

export interface CompletedQuestion {
  question_id: string;
  answer: string;
}

export interface ConditionExecutionStatus {
  condition_id: string;
  status: "executable_filter" | "request_only" | "unrestricted" | "pending";
  value: string | null;
  reason: string;
}

export interface PreparationState extends PreparationToken {
  expires_at: string;
  mode: TaskType;
  original_request: string;
  topic: string | null;
  time_scope: TimeScope | null;
  area: string | null;
  dimensions: string[];
  confirmed_conditions: Condition[];
  pending_conditions: Condition[];
  completed_questions: CompletedQuestion[];
  clarification_count: number;
  condition_statuses: ConditionExecutionStatus[];
}

export interface PreparedRequest {
  task_type: TaskType;
  question: string;
  targets?: string[] | null;
  dimensions?: string[] | null;
}

export interface PreparationResponse {
  status: PreparationStatus;
  mode: TaskType;
  message: string;
  options: PreparationOption[];
  state: PreparationState;
  request: PreparedRequest | null;
  warnings: string[];
}

export interface CatalogDatasetInfo {
  generation_id: string;
  revision: number;
  total_documents: number;
  partial: boolean | null;
  source: "current_published_frozen_release" | "demo_synthetic";
  region_version?: string | null;
  region_mapping_version?: string | null;
}

export type PolicyAdminLevel = "national" | "province" | "prefecture" | "county" | "unclassified";
export type PolicyRegionStatus = "mapped" | "unclassified" | "not_applicable";
export type PolicyFacetStatus = "unclassified";
export type PolicyYear = number | "unknown";

export interface PolicyVersionPin {
  generation_id: string;
  revision: number;
  region_version: string;
  region_mapping_version: string | "unavailable";
}

export interface PolicyFacetFilters {
  q?: string;
  area?: string;
  fyp?: string;
  plantype?: string;
  admin_level?: PolicyAdminLevel;
  province_code?: string;
  prefecture_code?: string;
  county_code?: string;
  region_status?: PolicyRegionStatus;
  year?: PolicyYear;
  fyp_status?: PolicyFacetStatus;
  plantype_status?: PolicyFacetStatus;
}

export type PolicyFacetRequest = PolicyFacetFilters & Partial<PolicyVersionPin>;
export type PolicyFacetQuery = { [Key in keyof PolicyFacetFilters]-?: PolicyFacetFilters[Key] | null };

export type RegionTier = "province" | "prefecture" | "county" | "unknown";

export interface RegionNode {
  code: string;
  name: string;
  source_type: string;
  tier: RegionTier;
  children: RegionNode[];
}

export interface PolicyFacetOption<TValue = string | number> {
  value: TValue;
  label: string;
  count: number;
}

export interface PolicyRegionOption {
  code: string;
  name: string;
  source_type: string;
  tier: Exclude<RegionTier, "unknown">;
  count: number;
}

export interface PolicyFacetsResponse {
  dataset: CatalogDatasetInfo & { region_version: string; region_mapping_version: string | null };
  query: PolicyFacetQuery;
  facets: {
    admin_level: {
      options: PolicyFacetOption<PolicyAdminLevel>[];
      regions: {
        province_options: PolicyRegionOption[];
        prefecture_options: PolicyRegionOption[];
        county_options: PolicyRegionOption[];
        unclassified: { label: string; count: number };
        not_applicable: { label: string; count: number };
      };
    };
    year: { options: PolicyFacetOption<PolicyYear>[] };
    fyp: { options: PolicyFacetOption<string>[]; unclassified: { label: string; count: number } };
    plantype: { options: PolicyFacetOption<string>[]; unclassified: { label: string; count: number } };
  };
}

export interface PolicyRegionsResponse {
  region_version: string;
  artifact_sha256: string;
  source: string;
  source_url: string;
  retrieved_at_utc: string;
  snapshot_as_of: string | null;
  official_version: string | null;
  node_count: number;
  data: RegionNode[];
}

export interface PolicyMatch {
  field: "title" | "body";
  offset: number;
  snippet: string;
}

export interface PolicyListItem {
  planid: string;
  title: string | null;
  area: string | null;
  fyp: string | null;
  year?: number | null;
  plantype_label: string | null;
  admin_level?: PolicyAdminLevel | null;
  region_code?: string | null;
  region_status?: PolicyRegionStatus | null;
  matches: PolicyMatch[];
}

export interface PolicyListResponse {
  dataset: CatalogDatasetInfo;
  query: {
    q: string | null;
    area: string | null;
    fyp: string | null;
    plantype: string | null;
    match_mode: "literal" | null;
    admin_level?: PolicyAdminLevel | null;
    province_code?: string | null;
    prefecture_code?: string | null;
    county_code?: string | null;
    region_status?: PolicyRegionStatus | null;
    year?: PolicyYear | null;
    fyp_status?: PolicyFacetStatus | null;
    plantype_status?: PolicyFacetStatus | null;
    generation_id?: string | null;
    revision?: number | null;
    region_version?: string | null;
    region_mapping_version?: string | null;
  };
  matched_documents: number;
  page: number;
  page_size: number;
  total_pages: number;
  items: PolicyListItem[];
}

export interface PolicySection {
  index: number;
  level: number;
  path: string;
  start: number;
  end: number;
}

export interface PolicyDetailResponse {
  dataset: CatalogDatasetInfo;
  policy: {
    planid: string;
    title: string | null;
    area: string | null;
    fyp: string | null;
    year?: number | null;
    plantype_label: string | null;
    body_chars: number;
    sections: PolicySection[];
  };
}

export interface PolicyContentResponse {
  dataset: CatalogDatasetInfo;
  planid: string;
  title: string | null;
  offset: number;
  limit: number;
  total_chars: number;
  has_more: boolean;
  text: string;
}

export interface PolicyListParams extends PolicyFacetFilters {
  page?: number;
  page_size?: number;
  generation_id?: string;
  revision?: number;
  region_version?: string;
  region_mapping_version?: string | "unavailable";
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

export interface QAAnalysisResult {
  schema_version: "qa-analysis/1";
  analyses: {
    kind: "synthesis" | "comparison" | "implication";
    text: string;
    scope_note: string;
    supporting_claim_indexes: number[];
    supporting_chunk_ids: string[];
    verification: "evidence_linked_only";
  }[];
  verification: {
    policy_facts: "existing_quote_and_number_checks";
    analysis: "dependency_links_checked_semantics_not_verified";
  };
}

export type StructuredResult =
  | OrganizeResult
  | QAAnalysisResult
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
