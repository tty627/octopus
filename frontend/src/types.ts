export type PageId = "home" | "search" | "tasks" | "documents" | "settings";
export type Readability = "readable" | "partial" | "low";
export type IndexingState = "indexed" | "metadata_only" | "failed";
export type SourceKind = "physical" | "archive" | "archive_member";
export type FreshnessStatus = "current" | "stale" | "unavailable" | "needs_review" | "changed" | "missing" | "unverified";
export type CitationStyle = "gb-t-7714-2015" | "apa";
export type TaskTemplateId = "literature_review" | "course_report" | "free_research";

export interface SourceRef {
  kind?: SourceKind;
  source_kind?: SourceKind;
  workspace_path: string;
  virtual_path: string;
  container_path?: string;
  member_path?: string;
  member_chain?: string[];
  member_indexes?: number[];
  archive_depth?: number;
  stable_id?: string;
}

export interface EvidenceLocator {
  kind: "page" | "paragraph" | "table" | "sheet" | "slide" | "image" | "text" | "unknown";
  page_number?: number | null;
  paragraph_index?: number | null;
  table_index?: number | null;
  sheet_name?: string;
  cell_range?: string;
  slide_number?: number | null;
  line_start?: number | null;
  line_end?: number | null;
  label?: string;
}

export interface CitationRecord {
  citation_id?: string;
  citation_type?: "article" | "book" | "chapter" | "conference" | "thesis" | "report" | "web" | "dataset" | "software" | "other";
  title: string;
  authors: string[];
  year: string;
  carrier: string;
  publication_title: string;
  place?: string;
  publisher?: string;
  volume?: string;
  issue?: string;
  pages: string;
  edition?: string;
  doi: string;
  url: string;
  accessed_at?: string;
  language?: string;
  confidence: number;
}

export interface BootstrapPayload {
  base_url: string;
  token: string;
  product_version: string;
  platform: string;
}

export interface WorkspaceHealth {
  document_count: number;
  readable_count: number;
  partial_count: number;
  low_quality_count: number;
  metadata_only_count: number;
  failed_count: number;
  last_sync_at: string;
}

export interface Workspace {
  workspace_id: string;
  name: string;
  raw_path: string;
  available: boolean;
  enabled: boolean;
  vision_enabled: boolean;
  legacy_index_present: boolean;
  health: WorkspaceHealth;
}

export interface WorkspaceDocument {
  document_id: string;
  name: string;
  relative_path: string;
  extension: string;
  content_hash: string;
  size_bytes: number;
  modified_at: string;
  title: string;
  overview: string;
  page_count: number;
  readability: Readability;
  readability_score: number;
  indexing_state: IndexingState;
  error: string;
  source_uri: string;
  source_ref?: SourceRef | null;
  locator?: EvidenceLocator | null;
  quality_flags?: string[];
  error_code?: string;
  parser_key?: string;
  parser_version?: string;
  freshness_status?: FreshnessStatus;
}

export interface WorkspaceEvidence {
  page_number: number | null;
  locator?: EvidenceLocator | null;
  heading: string;
  excerpt: string;
  reason: string;
  quality_score: number;
}

export interface SearchResultV2 {
  document_id: string;
  name: string;
  relative_path: string;
  extension: string;
  content_hash: string;
  size_bytes: number;
  modified_at: string;
  page_count: number;
  readability: Readability;
  readability_score: number;
  indexing_state: IndexingState;
  source_uri: string;
  source_ref?: SourceRef | null;
  locator?: EvidenceLocator | null;
  quality_flags?: string[];
  error_code?: string;
  parser_key?: string;
  parser_version?: string;
  freshness_status?: FreshnessStatus;
  overview: string;
  best_evidence: WorkspaceEvidence;
  additional_evidence: WorkspaceEvidence[];
  rank: number;
}

export interface SearchReportV2 {
  query: string;
  requested_mode: "local" | "assisted";
  actual_mode: "local" | "assisted" | "degraded";
  degradation_reason: string;
  answer: string;
  results: SearchResultV2[];
  candidate_count: number;
  duration_ms: number;
}

export interface SearchFiltersV2 {
  path_prefix: string;
  extensions: string[];
  source_kinds?: SourceKind[];
  readability?: Readability[];
  indexing_states?: IndexingState[];
  modified_from?: string;
  modified_to?: string;
  task_id?: string;
}

export type AIProviderId = "deepseek" | "openai_compatible";

export interface AISettingsV2 {
  workspace_id: string;
  enabled: boolean;
  provider: AIProviderId;
  base_url: string;
  model: string;
  credential_configured: boolean;
  credential_source: "windows_credential" | "environment" | "none";
  credential_error: string;
  vision_enabled: boolean;
}

export interface AISettingsInputV2 {
  enabled: boolean;
  provider: AIProviderId;
  base_url: string;
  model: string;
  api_key?: string;
  clear_api_key?: boolean;
}

export interface AIIndexStatus {
  workspace_id: string;
  document_count: number;
  indexed_document_count: number;
  pending_document_count: number;
  failed_document_count: number;
  folder_count: number;
  indexed_folder_count: number;
  pending_folder_count: number;
  failed_folder_count: number;
  estimated_calls: number;
  last_run_at: string;
  last_error: string;
}

export interface ResearchCandidate {
  candidate_id: string;
  document_id: string;
  content_hash: string;
  name: string;
  relative_path: string;
  page_number: number | null;
  locator?: EvidenceLocator | null;
  excerpt: string;
  reason: string;
  quality_score: number;
  source_ref?: SourceRef | null;
  overview: string;
}

export interface ResearchSlotProposal {
  name: string;
  description: string;
  required: boolean;
  candidate_ids: string[];
  rationales: Record<string, string>;
}

export interface ResearchTaskProposal {
  title: string;
  goal: string;
  summary: string;
  warnings: string[];
  gaps: string[];
  slots: ResearchSlotProposal[];
  candidates: ResearchCandidate[];
}

export interface AIConnectionResult {
  ok: boolean;
  code: string;
  message: string;
}

export interface WorkspaceTaskSlot {
  slot_id: string;
  name: string;
  description: string;
  position: number;
  required: boolean;
}

export interface WorkspaceTaskItem {
  item_id: string;
  document_id: string;
  content_hash: string;
  name: string;
  relative_path: string;
  page_number: number | null;
  excerpt: string;
  rationale: string;
  slot_id: string;
  review_state: "confirmed" | "pending";
  source_status: "resolved" | "source_unconfirmed";
  position: number;
  added_at?: string;
  source_ref?: SourceRef | null;
  locator?: EvidenceLocator | null;
  quality_flags?: string[];
  error_code?: string;
  citation?: CitationRecord | null;
  freshness_status?: FreshnessStatus;
  confirmed_content_hash?: string;
  verified_content_hash?: string;
  verified_at?: string;
}

export interface WorkspaceTask {
  schema_version: string;
  task_id: string;
  workspace_id: string;
  revision: number;
  lifecycle: "draft" | "saved" | "archived";
  title: string;
  goal: string;
  slots: WorkspaceTaskSlot[];
  items: WorkspaceTaskItem[];
  created_at: string;
  updated_at: string;
  migrated_from_v1: boolean;
  template_id?: TaskTemplateId;
  citation_style?: CitationStyle;
}

export interface WorkspaceTaskSummary {
  schema_version: string;
  task_id: string;
  workspace_id: string;
  revision: number;
  lifecycle: string;
  title: string;
  goal: string;
  item_count: number;
  pending_count: number;
  unresolved_count: number;
  stale_count?: number;
  updated_at: string;
  writable: boolean;
  template_id?: TaskTemplateId;
  // Compatibility with early 2.1 mock payloads.
  freshness_issue_count?: number;
}

export interface WorkspaceChange {
  change_id: string;
  workspace_id: string;
  kind: "added" | "modified" | "moved" | "deleted" | "parser_warning";
  document_id?: string;
  name: string;
  relative_path: string;
  previous_path?: string;
  occurred_at: string;
  message?: string;
  affected_task_ids?: string[];
  acknowledged?: boolean;
}

export interface OpenTargetResponse {
  uri: string;
  temporary: boolean;
  expires_at: string;
  display_name: string;
  source_ref?: SourceRef | null;
}

export interface ResearchPackExportRequest {
  citation_style: CitationStyle;
  include_sources: boolean;
}

export interface WorkspaceJobProgress {
  phase?: "discovering" | "processing" | "finalizing" | "completed";
  discovered?: number;
  processed?: number;
  current_file?: string;
  current_page?: number;
  page_count?: number;
  pages_completed?: number;
  ocr_pages_completed?: number;
  extraction_stage?: "pdfium" | "pypdf" | "ocr" | "page_complete";
  indexed?: number;
  unchanged?: number;
  failed?: number;
  removed?: number;
}

export interface ServiceJobResult extends Record<string, unknown> {
  progress?: WorkspaceJobProgress;
}

export interface ServiceJob {
  job_id: string;
  repository_id: string;
  kind: "workspace_sync" | "workspace_rebuild" | "workspace_ai_index" | "task_export" | "update" | "rebuild_search" | "validate" | "package";
  status: "queued" | "running" | "succeeded" | "failed" | "canceled" | "interrupted";
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  result: ServiceJobResult;
  error_code: string;
  error_message: string;
  cancel_requested?: boolean;
  resumed_from_job_id?: string;
}
