export type PageId = "workbench" | "search" | "task-packs" | "repositories";
export type IndexType = "text" | "leaf" | "foldernode";

export interface BootstrapPayload {
  base_url: string;
  token: string;
  product_version: string;
  platform: string;
}

export interface Repository {
  repository_id: string;
  name: string;
  raw_repository_path?: string;
  index_repository_path: string;
  available: boolean;
  enabled: boolean;
  last_successful_update_at?: string;
  states?: Record<string, number>;
  queues?: Record<string, unknown>;
  scan?: { scan_generation?: number; last_scan_at?: string };
}

export interface RepositoryEstimate {
  raw_path: string;
  index_path: string;
  file_count: number;
  directory_count: number;
  supported_file_count: number;
  unsupported_file_count: number;
  format_counts: Record<string, number>;
  total_source_bytes: number;
  estimated_index_bytes: number;
  required_free_bytes: number;
  available_free_bytes: number;
  estimated_seconds_p50: number;
  estimated_seconds_p95: number;
  blockers: string[];
  warnings: string[];
}

export interface EvidenceAnchor {
  locator: string;
  kind: string;
  text_excerpt?: string;
  extraction_method?: string;
  confidence?: number | null;
}

export interface MatchEvidence {
  field: "name" | "path" | "summary" | "keywords" | "evidence" | "body";
  locator: string;
  excerpt: string;
  matched_terms: string[];
}

export interface SearchResult {
  node_id: string;
  index_type: IndexType;
  index_path: string;
  raw_relative_path: string;
  name: string;
  summary: string;
  description: string;
  status: string;
  source_uri: string;
  content_id: string;
  modified_at: string;
  size_bytes: number;
  evidence: EvidenceAnchor[];
  quality_flags: string[];
  risk_flags: string[];
  rank: number;
  score: number;
  match_reasons: string[];
  match_evidence: MatchEvidence[];
  explanation: string;
  recommended_open_target: "index" | "source";
  open_target_uri: string;
}

export interface SearchFilters {
  index_types: IndexType[];
  path_prefix: string;
  statuses: string[];
  quality_flags: string[];
  modified_after: string;
  modified_before: string;
}

export interface SearchReport {
  query: string;
  requested_mode: "local" | "auto";
  actual_mode: "local" | "ai" | "degraded";
  degradation_reason: string;
  answer: {
    summary: string;
    recommended_node_ids: string[];
    warnings: string[];
    cited_node_ids: string[];
  };
  results: SearchResult[];
  candidate_count: number;
  duration_ms: number;
}

export interface TaskPackSlot {
  slot_id: string;
  name: string;
  description: string;
  position: number;
  required: boolean;
}

export interface TaskPackItem {
  item_id: string;
  node_id: string;
  name: string;
  index_type: IndexType;
  raw_relative_path: string;
  content_id: string;
  status_snapshot: string;
  anchors: EvidenceAnchor[];
  rationale: string;
  slot_id: string;
  review_state: "confirmed" | "pending";
  position: number;
  added_at?: string;
}

export interface TaskPack {
  schema_version: string;
  task_pack_id: string;
  repository_id: string;
  revision: number;
  lifecycle: "draft" | "saved" | "archived";
  title: string;
  goal: string;
  slots: TaskPackSlot[];
  items: TaskPackItem[];
  excluded_node_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface TaskPackSummary {
  schema_version: string;
  task_pack_id: string;
  repository_id: string;
  revision: number;
  lifecycle: string;
  title: string;
  goal: string;
  item_count: number;
  pending_count: number;
  updated_at: string;
  writable: boolean;
}

export interface ServiceJob {
  job_id: string;
  repository_id: string;
  kind: "update" | "rebuild_search" | "validate" | "package";
  status: "queued" | "running" | "succeeded" | "failed";
  result: Record<string, unknown>;
  error_code: string;
  error_message: string;
}

export interface ValidationReport {
  error_count: number;
  warning_count: number;
  issues: Array<{ severity: "warning" | "error"; code: string; message: string }>;
}
