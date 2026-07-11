from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


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


class GlobalConfig(OctopusModel):
    schema_version: str = "0.1"
    active_repository_id: str | None = None
    repositories: dict[str, GlobalRepository] = Field(default_factory=dict)


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
    generator_version: str = "0.1.0"
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
    generator_version: str = "0.1.0"


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


class ExtractedDocument(OctopusModel):
    name: str
    document_type: str
    text: str = ""
    structure: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    unsupported: bool = False


class GeneratedSummary(OctopusModel):
    one_sentence_summary: str
    description: str
    tag_rough: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    recommended_reading: list[str] = Field(default_factory=list)


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


class ContentParser(Protocol):
    def can_handle(self, path: Path) -> bool: ...

    def extract(self, path: Path) -> ExtractedDocument: ...


class AIProvider(Protocol):
    def generate_leaf(self, document: ExtractedDocument) -> GeneratedSummary: ...

    def summarize_folder(
        self, name: str, children: list[dict[str, Any]], previous: str = ""
    ) -> GeneratedSummary: ...

    def rerank_search(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...
