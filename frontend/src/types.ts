export type PageId = "search" | "tasks" | "documents" | "settings";
export type Readability = "readable" | "partial" | "low";
export type IndexingState = "indexed" | "metadata_only" | "failed";

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
}

export interface WorkspaceEvidence {
  page_number: number | null;
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
  updated_at: string;
  writable: boolean;
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
  kind: "workspace_sync" | "update" | "rebuild_search" | "validate" | "package";
  status: "queued" | "running" | "succeeded" | "failed";
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  result: ServiceJobResult;
  error_code: string;
  error_message: string;
}
