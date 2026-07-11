from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
    GeneratedSummary,
    NodeRecord,
    NodeState,
    utc_now,
)
from .parsers import ParserRegistry, TextParser, is_plain_text
from .providers import HeuristicProvider, create_provider
from .rendering import (
    collision_safe_path,
    foldernode_filename,
    leaf_filename,
    read_machine_header,
    render_foldernode,
    render_leaf,
    validate_index_text,
    write_url_shortcut,
)
from .scanner import RepositoryScanner, ScanOutcome
from .utils import atomic_write_json, atomic_write_text, sha256_file, stable_text_hash


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
        self.stats = UpdateStats(
            ai_provider="deepseek"
            if type(self.provider).__name__ == "DeepSeekProvider"
            else "heuristic"
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
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _write_shortcuts(self, node: NodeRecord) -> None:
        source = self._source_path(node)
        directory = self._mirror_directory(node)
        if node.node_kind == "raw_folder":
            path = directory / "打开原始文件夹.url"
        else:
            path = directory / f"{source.name}.url"
        ensure_outside_raw(path, self.raw)
        write_url_shortcut(path, source)

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
            atomic_write_text(destination, rendered)
            if old_path and old_path != destination:
                old_path.unlink(missing_ok=True)
            node.index_relative_path = destination.relative_to(self.index).as_posix()
            node.indexing.last_indexed_at = utc_now()
            node.indexing.last_successful_index_at = utc_now()
            node.indexing.last_error = ""
            node.indexing.error_code = ""
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
            recoverable = any(
                token in type(error).__name__
                for token in ("RateLimit", "APIConnection", "APITimeout", "InternalServer")
            )
            node.state = NodeState.retry if recoverable else NodeState.failed
            node.indexing.last_error = str(error)[:1000]
            node.indexing.error_code = type(error).__name__
            node.indexing.retry_count += 1
            if recoverable and node.node_id not in self.state.queues.retry:
                self.state.queues.retry.append(node.node_id)
            elif not recoverable and node.node_id not in self.state.queues.failed:
                self.state.queues.failed.append(node.node_id)
            self.stats.failed += 1
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
            control["index_status"] = node.state.value
            control["last_seen_at"] = node.stability.last_seen_at
            control["pending_reason"] = node.pending_reason
            text = json.dumps(header, ensure_ascii=False, indent=2) + "\n\n" + body
            validate_index_text(text, "leaf")
            atomic_write_text(path, text)
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
            "size_bytes": child.fingerprint.size_bytes,
            "quality_flags": [],
            "source_link_available": source.exists(),
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
                    document_or_folder_type=document.document_type,
                    tag_rough=summary.tag_rough,
                    topic_keywords=summary.topic_keywords,
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
            common["index_link"] = index_path.resolve().as_uri() if index_path.exists() else ""
            if index_path.exists():
                try:
                    header, _ = read_machine_header(index_path)
                    layer = header.get("summary_layer", {})
                    common.update(
                        one_sentence_summary=layer.get("one_sentence_summary", ""),
                        document_or_folder_type=layer.get("document_type")
                        or layer.get("folder_type", ""),
                        tag_rough=layer.get("tag_rough", []),
                        topic_keywords=layer.get("topic_keywords", []),
                        quality_flags=layer.get("quality_flags", []),
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
            atomic_write_text(destination, rendered)
            if old_path and old_path != destination:
                old_path.unlink(missing_ok=True)
            node.index_relative_path = destination.relative_to(self.index).as_posix()
            node.indexing.last_indexed_at = utc_now()
            node.indexing.last_successful_index_at = utc_now()
            node.indexing.last_error = ""
            node.indexing.error_code = ""
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
            recoverable = any(
                token in type(error).__name__
                for token in ("RateLimit", "APIConnection", "APITimeout", "InternalServer")
            )
            node.state = NodeState.retry if recoverable else NodeState.failed
            node.indexing.last_error = str(error)[:1000]
            node.indexing.error_code = type(error).__name__
            node.indexing.retry_count += 1
            if recoverable and node.node_id not in self.state.queues.retry:
                self.state.queues.retry.append(node.node_id)
            elif not recoverable and node.node_id not in self.state.queues.failed:
                self.state.queues.failed.append(node.node_id)
            self.stats.failed += 1
            self.logger.event(
                "foldernode_failed",
                node.node_id,
                before,
                node.state.value,
                error=str(error)[:1000],
            )

    def _begin_transaction(self) -> Path:
        transaction = octopus_dir(self.index) / "transactions" / "current.json"
        if transaction.exists():
            try:
                old = json.loads(transaction.read_text(encoding="utf-8"))
                if old.get("status") == "started":
                    self.logger.event(
                        "transaction_recovery",
                        message=(
                            f"Recovered incomplete run {old.get('run_id', 'unknown')}; "
                            "manifest remains authoritative"
                        ),
                    )
            except (OSError, json.JSONDecodeError):
                pass
        atomic_write_json(
            transaction,
            {
                "run_id": f"scan-{self.state.scan.scan_generation + 1}",
                "status": "started",
                "started_at": utc_now(),
            },
        )
        return transaction

    def _commit(self, transaction: Path) -> None:
        self.state.repository.last_successful_update_at = utc_now()
        atomic_write_json(
            repository_state_path(self.index), self.state.model_dump(mode="json", by_alias=True)
        )
        atomic_write_json(
            transaction,
            {
                "run_id": f"scan-{self.state.scan.scan_generation}",
                "status": "committed",
                "committed_at": utc_now(),
            },
        )

    def run(
        self,
        *,
        scan_only: bool = False,
        leaf_only: bool = False,
        foldernode_only: bool = False,
        retry_only: bool = False,
        force_path: str | None = None,
    ) -> UpdateStats:
        lock_path = octopus_dir(self.index) / "update.lock"
        with RepositoryLock(
            lock_path,
            "update",
            self.config.repository.raw_repo_id,
            self.index,
        ):
            transaction = self._begin_transaction()
            scanner = RepositoryScanner(self.config)
            self.state, outcome = scanner.scan(self.state, force_path=force_path)
            self._apply_scan_stats(outcome)
            if scan_only:
                self._commit(transaction)
                self.logger.run_summary(asdict(self.stats))
                return self.stats

            file_nodes = [
                node for node in self.state.nodes.values() if node.node_kind == "raw_file"
            ]
            for node in file_nodes:
                if node.state not in {NodeState.queued, NodeState.moved, NodeState.indexing}:
                    self._sync_existing_leaf_status(node)
                should_process = node.state in {NodeState.queued, NodeState.moved}
                if retry_only:
                    should_process = node.state in {NodeState.failed, NodeState.retry}
                if foldernode_only:
                    should_process = False
                if should_process:
                    self._process_leaf(node)

            if not leaf_only:
                folder_nodes = [
                    node
                    for node in self.state.nodes.values()
                    if node.node_kind == "raw_folder" and node.state != NodeState.orphaned
                ]
                folder_nodes.sort(
                    key=lambda item: len(Path(item.raw_relative_path).parts), reverse=True
                )
                for node in folder_nodes:
                    self._process_folder(node)

            self._commit(transaction)
            try:
                from .search import SearchIndex

                SearchIndex(self.index).rebuild()
            except Exception as error:
                self.logger.event("search_rebuild_failed", error=str(error)[:1000])
                self.stats.failed += 1
            self.logger.run_summary(asdict(self.stats))
            return self.stats

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
