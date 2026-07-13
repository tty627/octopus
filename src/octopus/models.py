from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, computed_field

from . import __version__
from .prompts import PROMPT_VERSION


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class OctopusModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class NodeState(StrEnum):
    unknown = "unknown"
    clean = "clean"
    dirty = "dirty"
    editing = "editing"
    pending_edit = "pending_edit"
    pending_stable = "pending_stable"
    queued = "queued"
    indexing = "indexing"
    indexed = "indexed"
    failed = "failed"
    retry = "retry"
    ignored = "ignored"
    deleted = "deleted"
    moved = "moved"
    stale = "stale"
    orphaned = "orphaned"


class SchemaInfo(OctopusModel):
    octopus_schema: str = "0.2"
    config_type: str | None = None
    manifest_type: str | None = None
    index_type: Literal["leaf", "foldernode"] | None = None
    json_role: str | None = None


class RepositoryIdentity(OctopusModel):
    raw_repo_id: str
    raw_repository_path: str
    index_repository_path: str
    repository_name: str


class WatcherConfig(OctopusModel):
    enabled: bool = True
    scan_interval_minutes: int = 5
    allowed_scan_interval_minutes: list[int] = Field(default_factory=lambda: [1, 5, 15, 60])
    initial_scan_on_startup: bool = True
    run_once_mode_available: bool = True


class StabilityConfig(OctopusModel):
    minimum_quiet_seconds: int = 120
    required_stable_scan_count: int = 2
    pending_edit_max_hours: int = 24
    allow_stable_readonly_open_files: bool = True
    strictly_defer_suspected_editing_files: bool = True


class UpdatePolicy(OctopusModel):
    generation_order: str = "bottom_up"
    leaf_update_priority: str = "high"
    foldernode_mechanical_update_priority: str = "high"
    foldernode_ai_summary_update_priority: str = "throttled_by_depth"
    root_foldernode_ai_summary_policy: str = "batch_after_leaf_updates"
    preserve_old_index_on_failure: bool = True
    orphan_retention_days: int = 30


class AIConfig(OctopusModel):
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    complex_model: str = "deepseek-v4-pro"
    max_calls_per_run: int = 20
    concurrency: int = 2
    max_transport_retries: int = 3
    json_repair_attempts: int = 1
    enabled: bool = True
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)
    prompt_version: str = PROMPT_VERSION
    max_input_characters_per_request: int = Field(default=80_000, ge=1, le=1_000_000)
    max_output_tokens_per_request: int = Field(default=2_000, ge=1, le=32_000)
    max_input_tokens_per_run: int | None = Field(default=None, ge=1)
    max_output_tokens_per_run: int | None = Field(default=None, ge=1)
    max_estimated_cost_per_run: float | None = Field(default=None, ge=0)
    max_search_candidates: int = Field(default=30, ge=1, le=100)
    max_folder_children_per_request: int = Field(default=500, ge=1, le=5_000)


class IgnoreConfig(OctopusModel):
    default_ignore_rules_enabled: bool = True
    extra_exclude_globs: list[str] = Field(default_factory=list)
    include_overrides: list[str] = Field(default_factory=list)
    deprecated_folder_names: list[str] = Field(
        default_factory=lambda: ["废弃方案存放地", "deprecated", "archive", "old", "backup"]
    )


class RepositoryConfig(OctopusModel):
    schema_: SchemaInfo = Field(
        alias="schema",
        default_factory=lambda: SchemaInfo(config_type="repository_auto_update_config"),
    )
    repository: RepositoryIdentity
    watcher: WatcherConfig = Field(default_factory=WatcherConfig)
    stability: StabilityConfig = Field(default_factory=StabilityConfig)
    update_policy: UpdatePolicy = Field(default_factory=UpdatePolicy)
    ai_policy: AIConfig = Field(default_factory=AIConfig)
    ignore: IgnoreConfig = Field(default_factory=IgnoreConfig)


class GlobalRepository(OctopusModel):
    raw_repo_id: str
    name: str
    index_repository_path: str
    enabled: bool = True


class ServiceConfig(OctopusModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)
    scheduler_enabled: bool = True
    max_background_workers: int = Field(default=2, ge=1, le=8)
    allowed_origins: list[str] = Field(default_factory=list)


class GlobalConfig(OctopusModel):
    schema_version: str = "0.1"
    active_repository_id: str | None = None
    repositories: dict[str, GlobalRepository] = Field(default_factory=dict)
    service: ServiceConfig = Field(default_factory=ServiceConfig)


class Fingerprint(OctopusModel):
    size_bytes: int = 0
    modified_at: str = ""
    created_at: str = ""
    quick_hash: str = ""
    content_hash: str = ""
    fingerprint_version: str = "0.1"


class NodeStability(OctopusModel):
    last_seen_at: str = ""
    stable_scan_count: int = 0
    last_unstable_at: str = ""
    editing_signals: list[str] = Field(default_factory=list)
    pending_since: str = ""
    pending_deadline_at: str = ""


class IndexingInfo(OctopusModel):
    last_indexed_at: str = ""
    last_successful_index_at: str = ""
    last_attempt_at: str = ""
    retry_count: int = 0
    last_error: str = ""
    error_code: str = ""
    generator_version: str = __version__
    section_hashes: dict[str, str] = Field(default_factory=dict)


class DependencyInfo(OctopusModel):
    direct_parent_foldernode_id: str = ""
    ancestor_foldernode_ids: list[str] = Field(default_factory=list)
    dirty_reason: str = ""


class NodeRecord(OctopusModel):
    node_id: str
    node_kind: Literal["raw_file", "raw_folder", "leaf", "foldernode", "opaque_leaf_folder"]
    raw_relative_path: str
    index_relative_path: str = ""
    parent_node_id: str = ""
    child_node_ids: list[str] = Field(default_factory=list)
    state: NodeState = NodeState.unknown
    previous_state: NodeState | None = None
    fingerprint: Fingerprint = Field(default_factory=Fingerprint)
    stability: NodeStability = Field(default_factory=NodeStability)
    indexing: IndexingInfo = Field(default_factory=IndexingInfo)
    dependency: DependencyInfo = Field(default_factory=DependencyInfo)
    pending_reason: str = ""


class QueueState(OctopusModel):
    pending_edit: list[str] = Field(default_factory=list)
    leaf_update: list[str] = Field(default_factory=list)
    foldernode_mechanical_update: list[str] = Field(default_factory=list)
    foldernode_ai_summary_update: list[str] = Field(default_factory=list)
    retry: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    move_or_rename: list[str] = Field(default_factory=list)


class ManifestRepository(OctopusModel):
    raw_repo_id: str
    raw_repository_path_snapshot: str
    index_repository_path_snapshot: str
    created_at: str = Field(default_factory=utc_now)
    last_scan_started_at: str = ""
    last_scan_finished_at: str = ""
    last_successful_update_at: str = ""


class ScanInfo(OctopusModel):
    scan_generation: int = 0
    scan_interval_minutes: int = 5
    last_scan_status: Literal["clean", "partial", "failed"] = "clean"
    last_scan_error: str = ""


class RepositoryState(OctopusModel):
    schema_: SchemaInfo = Field(
        alias="schema", default_factory=lambda: SchemaInfo(manifest_type="repository_state")
    )
    repository: ManifestRepository
    scan: ScanInfo = Field(default_factory=ScanInfo)
    nodes: dict[str, NodeRecord] = Field(default_factory=dict)
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    queues: QueueState = Field(default_factory=QueueState)


class UpdateControl(OctopusModel):
    index_status: NodeState = NodeState.unknown
    last_seen_at: str = ""
    last_indexed_at: str = ""
    last_mechanical_update_at: str = ""
    last_ai_summary_update_at: str = ""
    raw_fingerprint: str = ""
    content_snapshot_id: str = ""
    pending_reason: str = ""
    dirty_reasons: list[str] = Field(default_factory=list)
    pending_child_count: int = 0
    failed_child_count: int = 0
    generator_version: str = __version__


class LeafHeader(OctopusModel):
    schema_: SchemaInfo = Field(
        alias="schema",
        default_factory=lambda: SchemaInfo(index_type="leaf", json_role="unified_machine_header"),
    )
    summary_layer: dict[str, Any]
    attachment_card_layer: dict[str, Any]
    extraction_policy: dict[str, Any]
    update_control: UpdateControl


class FolderNodeHeader(OctopusModel):
    schema_: SchemaInfo = Field(
        alias="schema",
        default_factory=lambda: SchemaInfo(
            index_type="foldernode", json_role="unified_machine_header"
        ),
    )
    summary_layer: dict[str, Any]
    folder_card_layer: dict[str, Any]
    children_summary_layer: dict[str, Any]
    aggregation_policy: dict[str, Any]
    extraction_policy: dict[str, Any]
    update_control: UpdateControl


class ExtractionEvidence(OctopusModel):
    locator: str
    kind: str
    text_excerpt: str = ""
    extraction_method: str = "native"
    confidence: float | None = None


class ExtractedDocument(OctopusModel):
    name: str
    document_type: str
    text: str = ""
    structure: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    unsupported: bool = False
    parser_version: str = __version__
    text_characters: int = 0
    truncated: bool = False
    evidence: list[ExtractionEvidence] = Field(default_factory=list)
    extraction_stats: dict[str, int | float | str | bool] = Field(default_factory=dict)


class GeneratedSummary(OctopusModel):
    one_sentence_summary: str
    description: str
    tag_rough: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    recommended_reading: list[str] = Field(default_factory=list)


class AIUsage(OctopusModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    models: dict[str, int] = Field(default_factory=dict)
    prompt_versions: dict[str, int] = Field(default_factory=dict)
    purposes: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, int] = Field(default_factory=dict)
    estimated_cost: float | None = None

    def add(self, other: AIUsage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.duration_ms += other.duration_ms
        for key, value in other.models.items():
            self.models[key] = self.models.get(key, 0) + value
        for key, value in other.prompt_versions.items():
            self.prompt_versions[key] = self.prompt_versions.get(key, 0) + value
        for key, value in other.purposes.items():
            self.purposes[key] = self.purposes.get(key, 0) + value
        for key, value in other.errors.items():
            self.errors[key] = self.errors.get(key, 0) + value
        if other.estimated_cost is not None:
            self.estimated_cost = (self.estimated_cost or 0.0) + other.estimated_cost


class SearchDocument(OctopusModel):
    node_id: str
    index_type: Literal["leaf", "foldernode"]
    index_path: str
    name: str
    summary: str
    description: str
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    body_excerpt: str = ""
    status: str = "clean"
    source_uri: str = ""


class SearchResult(SearchDocument):
    score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)
    match_reasons: list[str] = Field(default_factory=list)


class SearchCitation(OctopusModel):
    citation_id: str
    node_id: str
    name: str
    index_type: Literal["leaf", "foldernode"]
    index_path: str
    status: str = "clean"
    summary: str = ""


class GeneratedSearchAnswer(OctopusModel):
    summary: str
    recommended_node_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cited_node_ids: list[str] = Field(default_factory=list)


class SearchReport(OctopusModel):
    query: str
    answer: GeneratedSearchAnswer
    results: list[SearchResult]
    citations: list[SearchCitation] = Field(default_factory=list)
    ai_usage: AIUsage = Field(default_factory=AIUsage)


class TransactionStatus(StrEnum):
    started = "started"
    staged = "staged"
    committing = "committing"
    committed = "committed"
    rolled_back = "rolled_back"
    recovery_required = "recovery_required"


class TransactionOperation(OctopusModel):
    relative_path: str
    action: Literal["write", "delete"] = "write"
    staged_relative_path: str = ""
    backup_relative_path: str = ""
    existed_before: bool = False
    applied: bool = False
    is_manifest: bool = False


class TransactionRecord(OctopusModel):
    run_id: str
    status: TransactionStatus = TransactionStatus.started
    started_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    manifest_committed: bool = False
    operations: list[TransactionOperation] = Field(default_factory=list)
    error: str = ""


class RunReport(OctopusModel):
    run_id: str
    version: str = __version__
    repository_id: str
    started_at: str
    finished_at: str
    duration_ms: int = 0
    status: Literal["success", "partial", "failed", "dry_run"]
    stats: dict[str, Any] = Field(default_factory=dict)
    ai_usage: AIUsage = Field(default_factory=AIUsage)
    errors: list[dict[str, str]] = Field(default_factory=list)
    recovery_actions: list[str] = Field(default_factory=list)
    dry_run: bool = False


class DryRunPlan(OctopusModel):
    scan_generation: int
    discovered: int
    new: int
    modified: int
    moved: int
    deleted: int
    pending: int
    stability: dict[str, str] = Field(default_factory=dict)
    leaf_updates: list[str] = Field(default_factory=list)
    text_updates: list[str] = Field(default_factory=list)
    foldernode_updates: list[str] = Field(default_factory=list)
    estimated_ai_calls: int = 0


class ValidationSeverity(StrEnum):
    warning = "warning"
    error = "error"


class ValidationIssue(OctopusModel):
    severity: ValidationSeverity
    code: str
    message: str
    path: str = ""


class ValidationReport(OctopusModel):
    repository_id: str
    checked_at: str = Field(default_factory=utc_now)
    issues: list[ValidationIssue] = Field(default_factory=list)
    markdown_indexes: int = 0
    manifest_nodes: int = 0
    search_documents: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_count(self) -> int:
        return sum(issue.severity == ValidationSeverity.error for issue in self.issues)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warning_count(self) -> int:
        return sum(issue.severity == ValidationSeverity.warning for issue in self.issues)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ServiceJob(OctopusModel):
    job_id: str
    repository_id: str
    kind: Literal["update", "rebuild_search", "validate"]
    status: JobStatus = JobStatus.queued
    created_at: str = Field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""


class ContentParser(Protocol):
    def can_handle(self, path: Path) -> bool: ...

    def extract(self, path: Path) -> ExtractedDocument: ...


class AIProvider(Protocol):
    usage: AIUsage

    def generate_leaf(self, document: ExtractedDocument) -> GeneratedSummary: ...

    def summarize_folder(
        self, name: str, children: list[dict[str, Any]], previous: str = ""
    ) -> GeneratedSummary: ...

    def rerank_search(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...

    def compose_search(self, query: str, results: list[SearchResult]) -> GeneratedSearchAnswer: ...
