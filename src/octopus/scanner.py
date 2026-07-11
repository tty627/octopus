from __future__ import annotations

import fnmatch
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .filesystem import is_reparse_point
from .models import (
    DependencyInfo,
    Fingerprint,
    NodeRecord,
    NodeState,
    QueueState,
    RepositoryConfig,
    RepositoryState,
    utc_now,
)
from .utils import quick_hash_file

DEFAULT_IGNORED_DIRECTORIES = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".next",
    "dist",
    "build",
    "coverage",
}
DEFAULT_IGNORED_GLOBS = (
    "*.tmp",
    "*.temp",
    "*.swp",
    "*.lock",
    "~$*",
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
)


@dataclass
class ScanEntry:
    relative_path: str
    path: Path
    is_directory: bool
    fingerprint: Fingerprint
    editing_signals: list[str] = field(default_factory=list)


@dataclass
class ScanOutcome:
    discovered: int = 0
    new: int = 0
    modified: int = 0
    moved: int = 0
    deleted: int = 0
    pending: int = 0
    queued: int = 0
    ignored: int = 0


class RepositoryScanner:
    def __init__(self, config: RepositoryConfig) -> None:
        self.config = config
        self.raw = Path(config.repository.raw_repository_path).resolve()

    def _is_ignored(self, relative: str, name: str, is_directory: bool) -> bool:
        normalized = relative.replace("\\", "/")
        for override in self.config.ignore.include_overrides:
            if fnmatch.fnmatch(normalized, override):
                return False
        if is_directory:
            if name in DEFAULT_IGNORED_DIRECTORIES:
                return True
            if name.casefold() in {
                item.casefold() for item in self.config.ignore.deprecated_folder_names
            }:
                return True
        patterns = list(DEFAULT_IGNORED_GLOBS) + self.config.ignore.extra_exclude_globs
        return any(
            fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(normalized, pattern)
            for pattern in patterns
        )

    def _entries(self, outcome: ScanOutcome) -> dict[str, ScanEntry]:
        entries: dict[str, ScanEntry] = {}

        def visit(directory: Path) -> None:
            try:
                children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
            except (OSError, PermissionError):
                return
            lock_targets = {
                item.name[2:]
                for item in children
                if item.name.startswith("~$") and len(item.name) > 2
            }
            for child in children:
                path = Path(child.path)
                relative = path.relative_to(self.raw).as_posix()
                try:
                    is_directory = child.is_dir(follow_symlinks=False)
                except OSError:
                    outcome.ignored += 1
                    continue
                if self._is_ignored(relative, child.name, is_directory):
                    outcome.ignored += 1
                    continue
                if child.is_symlink() or is_reparse_point(path):
                    outcome.ignored += 1
                    continue
                try:
                    stat = child.stat(follow_symlinks=False)
                    modified = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
                    created = datetime.fromtimestamp(stat.st_ctime, UTC).isoformat()
                    quick = ""
                    if not is_directory:
                        quick = quick_hash_file(path)
                    fingerprint = Fingerprint(
                        size_bytes=0 if is_directory else stat.st_size,
                        modified_at=modified,
                        created_at=created,
                        quick_hash=quick,
                    )
                except (OSError, PermissionError):
                    outcome.ignored += 1
                    continue
                signals: list[str] = []
                if child.name in lock_targets:
                    signals.append("office_temporary_lock")
                entries[relative] = ScanEntry(relative, path, is_directory, fingerprint, signals)
                if is_directory:
                    visit(path)

        visit(self.raw)
        root_stat = self.raw.stat()
        entries[""] = ScanEntry(
            relative_path="",
            path=self.raw,
            is_directory=True,
            fingerprint=Fingerprint(
                modified_at=datetime.fromtimestamp(root_stat.st_mtime, UTC).isoformat(),
                created_at=datetime.fromtimestamp(root_stat.st_ctime, UTC).isoformat(),
            ),
        )
        outcome.discovered = len(entries)
        return entries

    def scan(
        self, state: RepositoryState, force_path: str | None = None
    ) -> tuple[RepositoryState, ScanOutcome]:
        outcome = ScanOutcome()
        state.repository.last_scan_started_at = utc_now()
        state.scan.scan_generation += 1
        state.queues = QueueState()
        entries = self._entries(outcome)
        old_by_path = {record.raw_relative_path: record for record in state.nodes.values()}
        now = datetime.now(UTC)
        quiet_threshold = now - timedelta(seconds=self.config.stability.minimum_quiet_seconds)
        pending_deadline = now + timedelta(hours=self.config.stability.pending_edit_max_hours)
        records_by_path: dict[str, NodeRecord] = {}
        new_paths: list[str] = []

        for relative, entry in entries.items():
            old = old_by_path.get(relative)
            is_directory = entry.is_directory
            unchanged = bool(
                old
                and old.fingerprint.size_bytes == entry.fingerprint.size_bytes
                and old.fingerprint.modified_at == entry.fingerprint.modified_at
                and old.fingerprint.quick_hash == entry.fingerprint.quick_hash
            )
            if old is None:
                node = NodeRecord(
                    node_id=str(uuid.uuid4()),
                    node_kind="raw_folder" if is_directory else "raw_file",
                    raw_relative_path=relative,
                    fingerprint=entry.fingerprint,
                )
                new_paths.append(relative)
                outcome.new += 1
            else:
                node = old.model_copy(deep=True)
                node.previous_state = node.state
                if unchanged:
                    entry.fingerprint.content_hash = old.fingerprint.content_hash
                node.fingerprint = entry.fingerprint

            node.stability.last_seen_at = utc_now()
            node.stability.editing_signals = entry.editing_signals
            modified_at = datetime.fromisoformat(entry.fingerprint.modified_at)
            forced = force_path is not None and (
                force_path == "*" or relative == force_path.replace("\\", "/").strip("/")
            )
            if is_directory:
                node.state = NodeState.clean if unchanged else NodeState.dirty
                node.stability.stable_scan_count = self.config.stability.required_stable_scan_count
            elif entry.editing_signals:
                deadline_reached = False
                if node.stability.pending_deadline_at:
                    try:
                        deadline_reached = now >= datetime.fromisoformat(
                            node.stability.pending_deadline_at
                        )
                    except ValueError:
                        deadline_reached = False
                node.state = NodeState.stale if deadline_reached else NodeState.pending_edit
                node.pending_reason = ",".join(entry.editing_signals)
                node.stability.stable_scan_count = 0
                node.stability.pending_since = node.stability.pending_since or utc_now()
                node.stability.pending_deadline_at = (
                    node.stability.pending_deadline_at or pending_deadline.isoformat()
                )
                if not deadline_reached:
                    state.queues.pending_edit.append(node.node_id)
                outcome.pending += 1
            elif unchanged:
                if node.state in {NodeState.failed, NodeState.retry}:
                    if node.state == NodeState.retry:
                        state.queues.retry.append(node.node_id)
                    else:
                        state.queues.failed.append(node.node_id)
                elif node.state in {
                    NodeState.pending_edit,
                    NodeState.pending_stable,
                    NodeState.dirty,
                    NodeState.unknown,
                }:
                    node.stability.stable_scan_count += 1
                    if (
                        node.stability.stable_scan_count
                        >= self.config.stability.required_stable_scan_count
                        and modified_at <= quiet_threshold
                    ):
                        node.state = NodeState.queued
                        state.queues.leaf_update.append(node.node_id)
                        outcome.queued += 1
                    else:
                        node.state = NodeState.pending_stable
                        state.queues.pending_edit.append(node.node_id)
                        outcome.pending += 1
                else:
                    node.state = NodeState.clean
            else:
                outcome.modified += 1
                node.stability.last_unstable_at = utc_now()
                node.stability.stable_scan_count = 1 if modified_at <= quiet_threshold else 0
                if forced and modified_at <= quiet_threshold:
                    node.state = NodeState.queued
                    state.queues.leaf_update.append(node.node_id)
                    outcome.queued += 1
                else:
                    node.state = NodeState.pending_stable
                    node.pending_reason = "fingerprint_changed"
                    node.stability.pending_since = node.stability.pending_since or utc_now()
                    node.stability.pending_deadline_at = pending_deadline.isoformat()
                    state.queues.pending_edit.append(node.node_id)
                    outcome.pending += 1
            records_by_path[relative] = node

        deleted_records = [record for path, record in old_by_path.items() if path not in entries]
        new_file_records = [
            records_by_path[path]
            for path in new_paths
            if path in records_by_path and records_by_path[path].node_kind == "raw_file"
        ]
        for deleted in deleted_records:
            matches = [
                candidate
                for candidate in new_file_records
                if candidate.fingerprint.size_bytes == deleted.fingerprint.size_bytes
                and candidate.fingerprint.quick_hash
                and candidate.fingerprint.quick_hash == deleted.fingerprint.quick_hash
            ]
            if len(matches) == 1:
                candidate = matches[0]
                temporary_id = candidate.node_id
                candidate.node_id = deleted.node_id
                candidate.index_relative_path = deleted.index_relative_path
                candidate.indexing = deleted.indexing.model_copy(deep=True)
                candidate.fingerprint.content_hash = deleted.fingerprint.content_hash
                candidate.previous_state = deleted.state
                candidate.state = NodeState.moved
                state.queues.move_or_rename.append(candidate.node_id)
                new_file_records.remove(candidate)
                outcome.moved += 1
                for record in records_by_path.values():
                    if record.node_id == temporary_id and record is not candidate:
                        record.node_id = candidate.node_id
            else:
                deleted.previous_state = deleted.state
                deleted.state = NodeState.orphaned
                deleted.pending_reason = "raw_source_deleted"
                state.queues.deleted.append(deleted.node_id)
                records_by_path[f"__orphaned__/{deleted.node_id}"] = deleted
                outcome.deleted += 1

        path_to_id = {
            path: record.node_id
            for path, record in records_by_path.items()
            if not path.startswith("__orphaned__/")
        }
        for path, record in records_by_path.items():
            if path.startswith("__orphaned__/"):
                continue
            parent_path = Path(path).parent.as_posix() if path else ""
            if parent_path == ".":
                parent_path = ""
            if path:
                record.parent_node_id = path_to_id.get(parent_path, "")
            ancestor_ids: list[str] = []
            cursor = parent_path
            while cursor in path_to_id and path_to_id[cursor] not in ancestor_ids:
                ancestor_ids.append(path_to_id[cursor])
                if not cursor:
                    break
                cursor = Path(cursor).parent.as_posix()
                if cursor == ".":
                    cursor = ""
            record.dependency = DependencyInfo(
                direct_parent_foldernode_id=record.parent_node_id,
                ancestor_foldernode_ids=ancestor_ids,
                dirty_reason=record.dependency.dirty_reason,
            )
            record.child_node_ids = [
                candidate.node_id
                for candidate_path, candidate in records_by_path.items()
                if candidate_path != path and candidate.parent_node_id == record.node_id
            ]

        folder_records = [
            (path, record)
            for path, record in records_by_path.items()
            if not path.startswith("__orphaned__/") and record.node_kind == "raw_folder"
        ]
        for path, folder in folder_records:
            digest = hashlib.sha256()
            children = sorted(
                (
                    records_by_path[candidate_path]
                    for candidate_path in records_by_path
                    if not candidate_path.startswith("__orphaned__/")
                    and records_by_path[candidate_path].parent_node_id == folder.node_id
                ),
                key=lambda item: item.raw_relative_path,
            )
            for child in children:
                digest.update(child.raw_relative_path.encode("utf-8"))
                digest.update(child.fingerprint.quick_hash.encode("ascii"))
            snapshot = digest.hexdigest()
            old = old_by_path.get(path)
            if old and old.fingerprint.content_hash == snapshot and folder.state != NodeState.dirty:
                folder.state = NodeState.clean
            else:
                folder.state = NodeState.dirty
                folder.dependency.dirty_reason = "child_set_changed"
            folder.fingerprint.content_hash = snapshot
            state.queues.foldernode_mechanical_update.append(folder.node_id)

        state.nodes = {record.node_id: record for record in records_by_path.values()}
        state.dependencies = {
            record.node_id: list(record.child_node_ids)
            for record in state.nodes.values()
            if record.node_kind == "raw_folder"
        }
        state.repository.last_scan_finished_at = utc_now()
        state.scan.last_scan_status = "clean"
        return state, outcome
