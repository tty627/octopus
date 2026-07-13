from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import load_repository_config, load_repository_state, octopus_dir
from .models import (
    NodeState,
    ValidationIssue,
    ValidationReport,
    ValidationSeverity,
)
from .rendering import read_machine_header, validate_index_text
from .search import SEARCH_SCHEMA_VERSION


def _issue(
    report: ValidationReport,
    severity: ValidationSeverity,
    code: str,
    message: str,
    path: Path | str = "",
) -> None:
    report.issues.append(
        ValidationIssue(
            severity=severity,
            code=code,
            message=message,
            path=str(path),
        )
    )


def validate_repository(index_repository: Path) -> ValidationReport:
    index = index_repository.resolve()
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    raw = Path(config.repository.raw_repository_path).resolve()
    report = ValidationReport(
        repository_id=config.repository.raw_repo_id,
        manifest_nodes=len(state.nodes),
    )
    referenced_indexes: set[Path] = set()

    if not raw.exists():
        _issue(report, ValidationSeverity.error, "raw_missing", "Raw Repository is missing", raw)
    if raw == index or raw in index.parents or index in raw.parents:
        _issue(
            report,
            ValidationSeverity.error,
            "repository_overlap",
            "Raw and Index repositories overlap",
            index,
        )

    node_ids = set(state.nodes)
    for node in state.nodes.values():
        missing_children = [child for child in node.child_node_ids if child not in node_ids]
        if missing_children:
            _issue(
                report,
                ValidationSeverity.error,
                "missing_dependency",
                f"Manifest node references {len(missing_children)} missing children",
                node.raw_relative_path,
            )
        if node.state == NodeState.orphaned:
            _issue(
                report,
                ValidationSeverity.warning,
                "orphaned_node",
                "Manifest node has no current Raw source",
                node.raw_relative_path,
            )
        if not node.index_relative_path:
            continue
        index_path = index / Path(node.index_relative_path.replace("/", os.sep))
        referenced_indexes.add(index_path.resolve())
        if not index_path.exists():
            _issue(
                report,
                ValidationSeverity.error,
                "index_missing",
                "Manifest points to a missing Markdown index",
                index_path,
            )
            continue
        try:
            text = index_path.read_text(encoding="utf-8-sig")
            header, _ = read_machine_header(index_path)
            expected = "foldernode" if node.node_kind == "raw_folder" else "leaf"
            validate_index_text(text, expected)
            source = header.get("folder_card_layer", {}).get("source", {})
            if expected == "leaf":
                source = header.get("attachment_card_layer", {}).get("source", {})
                source_id = source.get("source_id")
            else:
                source_id = source.get("folder_id")
            if source_id != node.node_id:
                _issue(
                    report,
                    ValidationSeverity.error,
                    "node_id_mismatch",
                    "Markdown machine header and Manifest node IDs differ",
                    index_path,
                )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            _issue(
                report,
                ValidationSeverity.error,
                "invalid_index",
                f"Markdown index validation failed: {type(error).__name__}",
                index_path,
            )

    for parent_id, child_ids in state.dependencies.items():
        if parent_id not in node_ids or any(child_id not in node_ids for child_id in child_ids):
            _issue(
                report,
                ValidationSeverity.error,
                "invalid_dependency_graph",
                "Manifest dependency graph references a missing node",
                parent_id,
            )

    markdown_paths = [
        path for path in index.rglob("*.md") if octopus_dir(index) not in path.parents
    ]
    report.markdown_indexes = len(markdown_paths)
    for markdown_path in markdown_paths:
        if markdown_path.resolve() not in referenced_indexes:
            _issue(
                report,
                ValidationSeverity.warning,
                "untracked_index",
                "Markdown index is not referenced by the Manifest",
                markdown_path,
            )
    for shortcut in index.rglob("*.url"):
        try:
            text = shortcut.read_text(encoding="utf-8-sig")
            if not text.startswith("[InternetShortcut]\nURL=file:"):
                raise ValueError("invalid shortcut")
        except (OSError, ValueError):
            _issue(
                report,
                ValidationSeverity.warning,
                "invalid_shortcut",
                "Shortcut is not a valid local InternetShortcut",
                shortcut,
            )

    search_database = octopus_dir(index) / "search.sqlite3"
    if not search_database.exists():
        _issue(
            report,
            ValidationSeverity.warning,
            "search_cache_missing",
            "SQLite search cache is missing and can be rebuilt",
            search_database,
        )
    else:
        try:
            uri = f"file:{search_database.as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                report.search_documents = int(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                )
                try:
                    row = connection.execute(
                        "SELECT value FROM search_metadata WHERE key = 'schema_version'"
                    ).fetchone()
                    search_schema = str(row[0]) if row else ""
                except sqlite3.Error:
                    search_schema = ""
            if integrity != "ok":
                raise sqlite3.DatabaseError(str(integrity))
            if search_schema != SEARCH_SCHEMA_VERSION:
                _issue(
                    report,
                    ValidationSeverity.warning,
                    "search_cache_schema_outdated",
                    "SQLite cache schema is outdated and will rebuild automatically",
                    search_database,
                )
            if report.search_documents != report.markdown_indexes:
                _issue(
                    report,
                    ValidationSeverity.warning,
                    "search_cache_out_of_sync",
                    "SQLite document count differs from Markdown index count",
                    search_database,
                )
        except (sqlite3.Error, OSError) as error:
            _issue(
                report,
                ValidationSeverity.warning,
                "search_cache_invalid",
                f"SQLite cache failed read-only validation: {type(error).__name__}",
                search_database,
            )

    if raw.exists():
        generated_names = [
            path
            for path in raw.rglob("*")
            if path.name == ".octopus"
            or path.name.endswith("的叶子索引.md")
            or path.name.endswith("FolderNode索引总结.md")
        ]
        for path in generated_names:
            _issue(
                report,
                ValidationSeverity.error,
                "generated_content_in_raw",
                "Octopus-generated content was found inside Raw Repository",
                path,
            )
    return report
