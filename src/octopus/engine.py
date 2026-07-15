from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import __version__
from .config import (
    load_repository_config,
    load_repository_state,
    octopus_dir,
    repository_state_path,
)
from .filesystem import ensure_outside_raw
from .locking import RepositoryLock
from .logging import UpdateLogger
from .models import (
    DryRunPlan,
    GeneratedSummary,
    NodeRecord,
    NodeState,
    RunReport,
    UpdatePhase,
    UpdateProgress,
    utc_now,
)
from .parsers import ParserRegistry, TextParser, is_plain_text
from .progress import CancellationToken, ProgressCallback, UpdateCancelledError
from .providers import (
    HeuristicProvider,
    ProviderRateLimitError,
    ProviderTransientError,
    create_provider,
)
from .rendering import (
    collision_safe_path,
    foldernode_filename,
    leaf_filename,
    read_machine_header,
    render_foldernode,
    render_leaf,
    validate_index_text,
)
from .scanner import RepositoryScanner, ScanOutcome
from .transactions import (
    IndexTransaction,
    mark_transaction_complete,
    recover_transactions,
    write_run_report,
)
from .utils import sha256_file, stable_text_hash


@dataclass
class UpdateStats:
    scan_generation: int = 0
    discovered: int = 0
    new: int = 0
    modified: int = 0
    moved: int = 0
    deleted: int = 0
    pending: int = 0
    leaf_updated: int = 0
    foldernode_updated: int = 0
    search_refresh_mode: str = "none"
    search_documents_upserted: int = 0
    search_documents_deleted: int = 0
    search_refresh_ms: int = 0
    failed: int = 0
    ai_provider: str = "heuristic"


class UpdateEngine:
    def __init__(self, index_repository: Path) -> None:
        self.index = index_repository.expanduser().resolve()
        self.config = load_repository_config(self.index)
        self.raw = Path(self.config.repository.raw_repository_path).resolve()
        self.state = load_repository_state(self.index, self.config)
        self.registry = ParserRegistry()
        self.provider = create_provider(self.config)
        self.heuristic = HeuristicProvider()
        self.logger = UpdateLogger(octopus_dir(self.index))
        self.transaction: IndexTransaction | None = None
        self.run_errors: list[dict[str, str]] = []
        self.stats = UpdateStats(
            ai_provider=self.config.ai_policy.provider
            if type(self.provider) is not HeuristicProvider
            else "heuristic"
        )

    @staticmethod
    def _emit(
        callback: ProgressCallback | None,
        phase: UpdatePhase,
        completed: int = 0,
        total: int = 0,
        current_path: str = "",
        *,
        cancellable: bool = True,
    ) -> None:
        if callback is None:
            return
        progress_ranges = {
            UpdatePhase.preparing: (0.0, 0.0),
            UpdatePhase.scanning: (0.0, 20.0),
            UpdatePhase.leaf: (20.0, 70.0),
            UpdatePhase.foldernode: (70.0, 90.0),
            UpdatePhase.committing: (90.0, 90.0),
            UpdatePhase.search_rebuild: (95.0, 95.0),
            UpdatePhase.complete: (100.0, 100.0),
            UpdatePhase.cancelled: (100.0, 100.0),
            UpdatePhase.failed: (100.0, 100.0),
        }
        start, finish = progress_ranges[phase]
        ratio = 0.0 if total <= 0 else min(1.0, completed / total)
        percent = start + (finish - start) * ratio
        callback(
            UpdateProgress(
                phase=phase,
                completed=completed,
                total=total,
                current_path=current_path,
                percent=percent,
                cancellable=cancellable,
            )
        )

    def _node_by_path(self, relative: str) -> NodeRecord | None:
        return next(
            (node for node in self.state.nodes.values() if node.raw_relative_path == relative),
            None,
        )

    def _source_path(self, node: NodeRecord) -> Path:
        return self.raw / Path(node.raw_relative_path.replace("/", os.sep))

    def _mirror_directory(self, node: NodeRecord) -> Path:
        if node.node_kind == "raw_folder":
            relative = node.raw_relative_path
        else:
            relative = Path(node.raw_relative_path).parent.as_posix()
            if relative == ".":
                relative = ""
        directory = self.index / Path(relative.replace("/", os.sep))
        ensure_outside_raw(directory, self.raw)
        return directory

    def _write_shortcuts(self, node: NodeRecord) -> None:
        source = self._source_path(node)
        directory = self._mirror_directory(node)
        if node.node_kind == "raw_folder":
            path = directory / "打开原始文件夹.url"
        else:
            path = directory / f"{source.name}.url"
        ensure_outside_raw(path, self.raw)
        if self.transaction is None:
            raise RuntimeError("Index writes require an active transaction")
        self.transaction.write_text(path, f"[InternetShortcut]\nURL={source.resolve().as_uri()}\n")

    def _old_text(self, node: NodeRecord, fallback_path: Path) -> tuple[str | None, Path | None]:
        candidates: list[Path] = []
        if node.index_relative_path:
            candidates.append(self.index / Path(node.index_relative_path.replace("/", os.sep)))
        candidates.append(fallback_path)
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8-sig"), candidate
                except OSError:
                    continue
        return None, None

    def _stage_text(self, path: Path, text: str) -> None:
        if self.transaction is None:
            raise RuntimeError("Index writes require an active transaction")
        self.transaction.write_text(path, text)

    def _record_failure(self, node: NodeRecord, error: Exception) -> None:
        recoverable = isinstance(error, (ProviderRateLimitError, ProviderTransientError))
        node.state = NodeState.retry if recoverable else NodeState.failed
        node.indexing.last_error = str(error)[:1000]
        node.indexing.error_code = type(error).__name__
        node.indexing.retry_count += 1
        if recoverable and node.node_id not in self.state.queues.retry:
            self.state.queues.retry.append(node.node_id)
        elif not recoverable and node.node_id not in self.state.queues.failed:
            self.state.queues.failed.append(node.node_id)
        self.stats.failed += 1
        self.run_errors.append(
            {
                "node_id": node.node_id,
                "code": type(error).__name__,
                "message": str(error)[:500],
            }
        )

    def _process_leaf(self, node: NodeRecord) -> None:
        source = self._source_path(node)
        if not source.exists():
            node.previous_state = node.state
            node.state = NodeState.orphaned
            return
        self._write_shortcuts(node)
        if is_plain_text(source):
            node.previous_state = node.state
            node.state = NodeState.clean
            node.index_relative_path = ""
            node.indexing.last_successful_index_at = utc_now()
            return
        directory = self._mirror_directory(node)
        destination = collision_safe_path(
            directory / leaf_filename(source.name, node.node_id), node.node_id
        )
        old_text, old_path = self._old_text(node, destination)
        before = node.state.value
        node.previous_state = node.state
        node.state = NodeState.indexing
        node.indexing.last_attempt_at = utc_now()
        try:
            node.fingerprint.content_hash = sha256_file(source)
            document = self.registry.extract(source)
            summary = self.provider.generate_leaf(document)
            rendered = render_leaf(self.config, node, source, document, summary, old_text)
            validate_index_text(rendered, "leaf")
            self._stage_text(destination, rendered)
            if old_path and old_path != destination:
                if self.transaction is None:
                    raise RuntimeError("Index writes require an active transaction")
                self.transaction.schedule_delete(old_path)
            node.index_relative_path = destination.relative_to(self.index).as_posix()
            node.indexing.last_indexed_at = utc_now()
            node.indexing.last_successful_index_at = utc_now()
            node.indexing.last_error = ""
            node.indexing.error_code = ""
            node.indexing.generator_version = __version__
            node.indexing.section_hashes = {
                "generated_document": stable_text_hash(rendered),
            }
            node.state = NodeState.failed if document.unsupported else NodeState.clean
            if document.unsupported:
                node.indexing.last_error = "No content parser is available for this format"
                node.indexing.error_code = "unsupported_content_parser"
                self.state.queues.failed.append(node.node_id)
                self.stats.failed += 1
            else:
                self.stats.leaf_updated += 1
            self.logger.event(
                "leaf_updated",
                node.node_id,
                before,
                node.state.value,
                f"Indexed {node.raw_relative_path}",
            )
        except Exception as error:
            self._record_failure(node, error)
            self.logger.event(
                "leaf_failed",
                node.node_id,
                before,
                node.state.value,
                error=str(error)[:1000],
            )

    def _sync_existing_leaf_status(self, node: NodeRecord) -> None:
        if not node.index_relative_path:
            return
        path = self.index / Path(node.index_relative_path.replace("/", os.sep))
        if not path.exists():
            return
        try:
            header, body = read_machine_header(path)
            if header.get("schema", {}).get("index_type") != "leaf":
                return
            control = header.setdefault("update_control", {})
            values = {
                "index_status": node.state.value,
                "last_seen_at": node.stability.last_seen_at,
                "pending_reason": node.pending_reason,
            }
            if all(control.get(key, "") == value for key, value in values.items()):
                return
            control.update(values)
            text = json.dumps(header, ensure_ascii=False, indent=2) + "\n\n" + body
            validate_index_text(text, "leaf")
            self._stage_text(path, text)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.logger.event(
                "leaf_status_sync_failed",
                node.node_id,
                message=node.raw_relative_path,
                error=str(error)[:1000],
            )

    def _child_signal(self, child: NodeRecord) -> dict[str, Any]:
        source = self._source_path(child)
        common: dict[str, Any] = {
            "child_id": child.node_id,
            "name": source.name
            if child.raw_relative_path
            else self.config.repository.repository_name,
            "relative_name_or_path": child.raw_relative_path,
            "index_status": child.state.value,
            "content_id": child.fingerprint.content_hash
            or (sha256_file(source) if source.is_file() else ""),
            "size_bytes": child.fingerprint.size_bytes,
            "modified_at": child.fingerprint.modified_at,
            "quality_flags": [],
            "source_link_available": source.exists(),
            "source_uri": source.resolve().as_uri() if source.exists() else "",
            "open_recommendation": "medium",
            "index_link": "",
        }
        if child.node_kind == "raw_folder":
            common["node_type"] = "foldernode"
        elif source.exists() and is_plain_text(source):
            common["node_type"] = "file"
            try:
                document = TextParser().extract(source)
                summary = self.heuristic.generate_leaf(document)
                common.update(
                    one_sentence_summary=summary.one_sentence_summary,
                    description=summary.description,
                    document_or_folder_type=document.document_type,
                    tag_rough=summary.tag_rough,
                    topic_keywords=summary.topic_keywords,
                    extraction_evidence=[
                        item.model_dump(mode="json", exclude_none=True)
                        for item in document.evidence
                    ],
                    truncated=document.truncated,
                )
            except (OSError, UnicodeError) as error:
                common.update(
                    one_sentence_summary=f"无法读取文本文件：{type(error).__name__}",
                    document_or_folder_type="text",
                    tag_rough=[],
                    topic_keywords=[],
                    quality_flags=["text_read_failed"],
                )
            return common
        else:
            common["node_type"] = "leaf"
        if child.index_relative_path:
            index_path = self.index / Path(child.index_relative_path.replace("/", os.sep))
            readable_path = (
                self.transaction.staged_path_for(index_path) if self.transaction else None
            ) or index_path
            common["index_link"] = index_path.resolve().as_uri() if readable_path.exists() else ""
            if readable_path.exists():
                try:
                    header, _ = read_machine_header(readable_path)
                    layer = header.get("summary_layer", {})
                    common.update(
                        one_sentence_summary=layer.get("one_sentence_summary", ""),
                        description=layer.get("description", ""),
                        document_or_folder_type=layer.get("document_type")
                        or layer.get("folder_type", ""),
                        tag_rough=layer.get("tag_rough", []),
                        topic_keywords=layer.get("topic_keywords", []),
                        quality_flags=layer.get("quality_flags", []),
                        extraction_evidence=header.get("attachment_card_layer", {}).get(
                            "extraction_evidence", []
                        ),
                        truncated=header.get("extraction_policy", {}).get(
                            "truncated", False
                        ),
                    )
                    return common
                except (OSError, ValueError, json.JSONDecodeError):
                    common["quality_flags"] = ["invalid_or_missing_child_index"]
        common.setdefault("one_sentence_summary", "索引尚未生成或当前不可用。")
        common.setdefault("document_or_folder_type", "unknown")
        common.setdefault("tag_rough", [])
        common.setdefault("topic_keywords", [])
        return common

    def _tree_lines(self, folder: NodeRecord) -> list[str]:
        base = folder.raw_relative_path
        label = self._source_path(folder).name if base else self.config.repository.repository_name
        lines = [f"{label}/"]
        descendants = [
            node
            for node in self.state.nodes.values()
            if node.state != NodeState.orphaned
            and node.raw_relative_path != base
            and (not base or node.raw_relative_path.startswith(base.rstrip("/") + "/"))
        ]
        descendants.sort(key=lambda item: item.raw_relative_path.casefold())
        base_depth = len(Path(base).parts) if base else 0
        for item in descendants:
            depth = len(Path(item.raw_relative_path).parts) - base_depth - 1
            name = Path(item.raw_relative_path).name
            suffix = "/" if item.node_kind == "raw_folder" else ""
            lines.append(f"{'│  ' * max(depth, 0)}├─ {name}{suffix}")
        return lines

    def _existing_folder_summary(self, old_text: str | None) -> GeneratedSummary | None:
        if not old_text:
            return None
        try:
            header, _ = read_machine_header_from_text(old_text)
            layer = header.get("summary_layer", {})
            return GeneratedSummary(
                one_sentence_summary=layer.get("one_sentence_summary", ""),
                description=layer.get("description", ""),
                tag_rough=layer.get("tag_rough", []),
                topic_keywords=layer.get("topic_keywords", []),
                recommended_reading=[
                    item if isinstance(item, str) else str(item.get("name", ""))
                    for item in layer.get("recommended_entry_nodes", [])
                ],
            )
        except (ValueError, json.JSONDecodeError):
            return None

    def _process_folder(self, node: NodeRecord) -> None:
        source = self._source_path(node)
        if not source.exists():
            node.state = NodeState.orphaned
            return
        self._write_shortcuts(node)
        directory = self._mirror_directory(node)
        name = source.name if node.raw_relative_path else self.config.repository.repository_name
        destination = collision_safe_path(
            directory / foldernode_filename(name, node.node_id), node.node_id
        )
        old_text, old_path = self._old_text(node, destination)
        children_records = [
            self.state.nodes[child_id]
            for child_id in node.child_node_ids
            if child_id in self.state.nodes
            and self.state.nodes[child_id].state != NodeState.orphaned
        ]
        children_records.sort(key=lambda item: item.raw_relative_path.casefold())
        children = [self._child_signal(child) for child in children_records]
        previous = self._existing_folder_summary(old_text)
        before = node.state.value
        node.previous_state = node.state
        node.state = NodeState.indexing
        try:
            if previous and before == NodeState.clean.value:
                summary = previous
            else:
                summary = self.provider.summarize_folder(
                    name, children, previous.description if previous else ""
                )
            rendered = render_foldernode(
                self.config,
                node,
                source,
                children,
                summary,
                self._tree_lines(node),
                old_text,
            )
            validate_index_text(rendered, "foldernode")
            self._stage_text(destination, rendered)
            if old_path and old_path != destination:
                if self.transaction is None:
                    raise RuntimeError("Index writes require an active transaction")
                self.transaction.schedule_delete(old_path)
            node.index_relative_path = destination.relative_to(self.index).as_posix()
            node.indexing.last_indexed_at = utc_now()
            node.indexing.last_successful_index_at = utc_now()
            node.indexing.last_error = ""
            node.indexing.error_code = ""
            node.indexing.generator_version = __version__
            node.indexing.section_hashes = {"generated_document": stable_text_hash(rendered)}
            node.state = NodeState.clean
            self.stats.foldernode_updated += 1
            self.logger.event(
                "foldernode_updated",
                node.node_id,
                before,
                node.state.value,
                f"Indexed folder {node.raw_relative_path or '/'}",
            )
        except Exception as error:
            self._record_failure(node, error)
            self.logger.event(
                "foldernode_failed",
                node.node_id,
                before,
                node.state.value,
                error=str(error)[:1000],
            )

    def plan(self, *, force_path: str | None = None) -> DryRunPlan:
        planned_state = self.state.model_copy(deep=True)
        planned_state, outcome = RepositoryScanner(self.config).scan(
            planned_state, force_path=force_path
        )
        leaf_updates: list[str] = []
        text_updates: list[str] = []
        for node in planned_state.nodes.values():
            if node.node_kind != "raw_file" or node.state not in {
                NodeState.queued,
                NodeState.moved,
                NodeState.retry,
            }:
                continue
            source = self.raw / Path(node.raw_relative_path.replace("/", os.sep))
            if source.exists() and is_plain_text(source):
                text_updates.append(node.raw_relative_path)
            else:
                leaf_updates.append(node.raw_relative_path)
        folder_updates = [
            node.raw_relative_path
            for node in planned_state.nodes.values()
            if node.node_kind == "raw_folder"
            and node.state not in {NodeState.orphaned, NodeState.ignored}
        ]
        ai_folders = sum(
            1
            for node in planned_state.nodes.values()
            if node.node_kind == "raw_folder"
            and (node.state == NodeState.dirty or not node.index_relative_path)
        )
        return DryRunPlan(
            scan_generation=planned_state.scan.scan_generation,
            discovered=outcome.discovered,
            new=outcome.new,
            modified=outcome.modified,
            moved=outcome.moved,
            deleted=outcome.deleted,
            pending=outcome.pending,
            stability={
                node.raw_relative_path: node.state.value
                for node in sorted(
                    planned_state.nodes.values(), key=lambda item: item.raw_relative_path
                )
                if node.node_kind == "raw_file"
                and node.state
                in {
                    NodeState.pending_edit,
                    NodeState.pending_stable,
                    NodeState.queued,
                    NodeState.moved,
                    NodeState.retry,
                    NodeState.failed,
                }
            },
            leaf_updates=sorted(leaf_updates),
            text_updates=sorted(text_updates),
            foldernode_updates=sorted(folder_updates),
            estimated_ai_calls=(
                len(leaf_updates) + ai_folders if self.config.ai_policy.enabled else 0
            ),
        )

    def _report(
        self,
        run_id: str,
        started_at: str,
        status: Literal["success", "partial", "failed", "dry_run", "cancelled"],
        recovery_actions: list[str],
        *,
        dry_run: bool = False,
    ) -> RunReport:
        finished_at = utc_now()
        duration_ms = max(
            0,
            int(
                (
                    datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
                ).total_seconds()
                * 1000
            ),
        )
        return RunReport(
            run_id=run_id,
            repository_id=self.config.repository.raw_repo_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            status=status,
            stats=asdict(self.stats),
            ai_usage=self.provider.usage.model_copy(deep=True),
            errors=list(self.run_errors),
            recovery_actions=recovery_actions,
            dry_run=dry_run,
        )

    def run(
        self,
        *,
        scan_only: bool = False,
        leaf_only: bool = False,
        foldernode_only: bool = False,
        retry_only: bool = False,
        force_path: str | None = None,
        progress_callback: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> UpdateStats:
        run_id = uuid.uuid4().hex
        started_at = utc_now()
        recovery_actions: list[str] = []
        lock_path = octopus_dir(self.index) / "update.lock"
        transaction: IndexTransaction | None = None
        self._emit(progress_callback, UpdatePhase.preparing)
        try:
            with RepositoryLock(
                lock_path,
                "update",
                self.config.repository.raw_repo_id,
                self.index,
            ):
                recovery_actions = recover_transactions(self.index)
                derived_runs = [
                    action.split(":", 1)[1]
                    for action in recovery_actions
                    if action.startswith("complete-derived:")
                ]
                if derived_runs:
                    from .search import SearchIndex

                    SearchIndex(self.index).rebuild()
                    for recovered_run in derived_runs:
                        mark_transaction_complete(self.index, recovered_run)
                for action in recovery_actions:
                    self.logger.event("transaction_recovery", message=action)

                if cancellation_token:
                    cancellation_token.raise_if_cancelled()
                transaction = IndexTransaction(self.index, run_id=run_id)
                self.transaction = transaction
                scanner = RepositoryScanner(self.config)
                self.state, outcome = scanner.scan(
                    self.state,
                    force_path=force_path,
                    progress_callback=progress_callback,
                    cancellation_token=cancellation_token,
                )
                self._apply_scan_stats(outcome)

                if not scan_only:
                    file_nodes = [
                        node for node in self.state.nodes.values() if node.node_kind == "raw_file"
                    ]
                    leaf_total = sum(
                        (
                            node.state in {NodeState.failed, NodeState.retry}
                            if retry_only
                            else node.state in {NodeState.queued, NodeState.moved}
                        )
                        and not foldernode_only
                        for node in file_nodes
                    )
                    leaf_completed = 0
                    self._emit(progress_callback, UpdatePhase.leaf, 0, leaf_total)
                    for node in file_nodes:
                        if cancellation_token:
                            cancellation_token.raise_if_cancelled()
                        if node.state not in {
                            NodeState.queued,
                            NodeState.moved,
                            NodeState.indexing,
                        }:
                            self._sync_existing_leaf_status(node)
                        should_process = node.state in {NodeState.queued, NodeState.moved}
                        if retry_only:
                            should_process = node.state in {NodeState.failed, NodeState.retry}
                        if foldernode_only:
                            should_process = False
                        if should_process:
                            self._emit(
                                progress_callback,
                                UpdatePhase.leaf,
                                leaf_completed,
                                leaf_total,
                                node.raw_relative_path,
                            )
                            self._process_leaf(node)
                            leaf_completed += 1
                            self._emit(
                                progress_callback,
                                UpdatePhase.leaf,
                                leaf_completed,
                                leaf_total,
                                node.raw_relative_path,
                            )
                            if cancellation_token:
                                cancellation_token.raise_if_cancelled()

                    if not leaf_only:
                        folder_nodes = [
                            node
                            for node in self.state.nodes.values()
                            if node.node_kind == "raw_folder"
                            and node.state != NodeState.orphaned
                            and (
                                foldernode_only
                                or node.state == NodeState.dirty
                                or not node.index_relative_path
                            )
                        ]
                        folder_nodes.sort(
                            key=lambda item: len(Path(item.raw_relative_path).parts),
                            reverse=True,
                        )
                        folder_total = len(folder_nodes)
                        self._emit(progress_callback, UpdatePhase.foldernode, 0, folder_total)
                        for folder_completed, node in enumerate(folder_nodes):
                            if cancellation_token:
                                cancellation_token.raise_if_cancelled()
                            self._emit(
                                progress_callback,
                                UpdatePhase.foldernode,
                                folder_completed,
                                folder_total,
                                node.raw_relative_path,
                            )
                            self._process_folder(node)
                            self._emit(
                                progress_callback,
                                UpdatePhase.foldernode,
                                folder_completed + 1,
                                folder_total,
                                node.raw_relative_path,
                            )

                self.state.repository.last_successful_update_at = utc_now()
                manifest_text = (
                    json.dumps(
                        self.state.model_dump(mode="json", by_alias=True),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                if cancellation_token:
                    cancellation_token.raise_if_cancelled()
                self._emit(
                    progress_callback,
                    UpdatePhase.committing,
                    cancellable=False,
                )
                transaction.commit(repository_state_path(self.index), manifest_text)

                derived_ok = True
                if not scan_only:
                    self._emit(
                        progress_callback,
                        UpdatePhase.search_rebuild,
                        cancellable=False,
                    )
                    try:
                        from .search import SearchIndex

                        started_refresh = time.perf_counter()
                        changed_paths: list[Path] = []
                        deleted_paths: list[Path] = []
                        for operation in transaction.record.operations:
                            if operation.is_manifest or not operation.relative_path.endswith(".md"):
                                continue
                            target = self.index / Path(operation.relative_path)
                            if operation.action == "write":
                                changed_paths.append(target)
                            else:
                                deleted_paths.append(target)
                        refresh = SearchIndex(self.index).refresh(
                            changed_paths,
                            deleted_paths,
                            manifest_generation=str(self.state.scan.scan_generation),
                        )
                        self.stats.search_refresh_mode = str(refresh["mode"])
                        self.stats.search_documents_upserted = int(refresh["upserted"])
                        self.stats.search_documents_deleted = int(refresh["deleted"])
                        self.stats.search_refresh_ms = max(
                            0, int((time.perf_counter() - started_refresh) * 1_000)
                        )
                    except Exception as error:
                        derived_ok = False
                        self.logger.event("search_rebuild_failed", error=str(error)[:1000])
                        self.run_errors.append(
                            {
                                "node_id": "",
                                "code": type(error).__name__,
                                "message": str(error)[:500],
                            }
                        )
                        self.stats.failed += 1
                self.logger.run_summary(asdict(self.stats))
                if derived_ok:
                    mark_transaction_complete(self.index, run_id)
                status: Literal["success", "partial"] = (
                    "partial" if self.stats.failed else "success"
                )
                write_run_report(
                    self.index,
                    self._report(run_id, started_at, status, recovery_actions),
                )
                self._emit(
                    progress_callback,
                    UpdatePhase.complete,
                    1,
                    1,
                    cancellable=False,
                )
                return self.stats
        except UpdateCancelledError:
            if transaction is not None and not transaction.record.manifest_committed:
                with suppress(OSError, RuntimeError):
                    transaction.rollback()
            with suppress(FileExistsError, OSError):
                write_run_report(
                    self.index,
                    self._report(run_id, started_at, "cancelled", recovery_actions),
                )
            self._emit(
                progress_callback,
                UpdatePhase.cancelled,
                1,
                1,
                cancellable=False,
            )
            raise
        except Exception as error:
            self.run_errors.append(
                {
                    "node_id": "",
                    "code": type(error).__name__,
                    "message": str(error)[:500],
                }
            )
            report = self._report(run_id, started_at, "failed", recovery_actions)
            with suppress(FileExistsError, OSError):
                write_run_report(self.index, report)
            self._emit(
                progress_callback,
                UpdatePhase.failed,
                1,
                1,
                cancellable=False,
            )
            raise
        finally:
            self.transaction = None

    def _apply_scan_stats(self, outcome: ScanOutcome) -> None:
        self.stats.scan_generation = self.state.scan.scan_generation
        self.stats.discovered = outcome.discovered
        self.stats.new = outcome.new
        self.stats.modified = outcome.modified
        self.stats.moved = outcome.moved
        self.stats.deleted = outcome.deleted
        self.stats.pending = outcome.pending


def read_machine_header_from_text(text: str) -> tuple[dict[str, Any], str]:
    from .rendering import parse_machine_header

    return parse_machine_header(text)
