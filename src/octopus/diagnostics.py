from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sqlite3
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import load_repository_config, load_repository_state
from .migrations import GLOBAL_SCHEMA_VERSION, REPOSITORY_SCHEMA_VERSION
from .models import RunReport, utc_now
from .plugin_sdk import PLUGIN_API_VERSION
from .search import SEARCH_SCHEMA_VERSION
from .transactions import run_report_directory
from .utils import load_json

DIAGNOSTIC_SCHEMA_VERSION: Literal["1.0"] = "1.0"
DIAGNOSTIC_MAX_JSON_BYTES = 1_000_000
DIAGNOSTIC_ENTRY = "diagnostics.json"
CONSENT_ENTRY = "consent.json"
SAFE_STAT_KEYS = {
    "discovered",
    "new",
    "modified",
    "moved",
    "deleted",
    "pending",
    "leaf_updated",
    "foldernode_updated",
    "search_documents_upserted",
    "search_documents_deleted",
    "search_refresh_ms",
    "failed",
}


class DiagnosticRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_version: str
    status: str
    duration_ms: int = Field(ge=0)
    stats: dict[str, int]
    error_codes: list[str]
    recovery_action_count: int = Field(ge=0)
    ai_calls: int = Field(ge=0)
    ai_total_tokens: int = Field(ge=0)


class DiagnosticRepository(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_ref: str
    manifest_node_count: int = Field(ge=0)
    node_state_counts: dict[str, int]
    queue_counts: dict[str, int]
    last_scan_status: str
    recent_runs: list[DiagnosticRun]


class DiagnosticBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = DIAGNOSTIC_SCHEMA_VERSION
    product_version: str = __version__
    generated_at: str = Field(default_factory=utc_now)
    local_only: Literal[True] = True
    contains_file_content: Literal[False] = False
    contains_paths: Literal[False] = False
    upload_consent: Literal[False] = False
    environment: dict[str, str]
    contracts: dict[str, str]
    repositories: list[DiagnosticRepository]


class DiagnosticConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    explicit_consent: Literal[True] = True
    consented_at: str = Field(default_factory=utc_now)
    source_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: Literal["share_manually"] = "share_manually"


def _safe_code(value: object) -> str:
    normalized = str(value).strip().casefold()
    return normalized[:80] if re.fullmatch(r"[a-z0-9_.-]{1,80}", normalized) else "other"


def _safe_run(report: RunReport) -> DiagnosticRun:
    stats = {
        key: int(value)
        for key, value in report.stats.items()
        if key in SAFE_STAT_KEYS and isinstance(value, int) and value >= 0
    }
    return DiagnosticRun(
        product_version=report.version,
        status=_safe_code(report.status),
        duration_ms=max(0, report.duration_ms),
        stats=stats,
        error_codes=sorted({_safe_code(item.get("code", "other")) for item in report.errors}),
        recovery_action_count=len(report.recovery_actions),
        ai_calls=max(0, report.ai_usage.calls),
        ai_total_tokens=max(0, report.ai_usage.total_tokens),
    )


def _recent_runs(index: Path, limit: int = 10) -> list[DiagnosticRun]:
    directory = run_report_directory(index)
    candidates = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime)[-limit:]
    results: list[DiagnosticRun] = []
    for path in candidates:
        try:
            payload = load_json(path)
            if isinstance(payload, dict):
                results.append(_safe_run(RunReport.model_validate(payload)))
        except (OSError, ValueError, TypeError):
            continue
    return results


def _repository_diagnostic(index: Path, number: int) -> DiagnosticRepository:
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    node_states = Counter(node.state.value for node in state.nodes.values())
    queues = {
        "pending_edit": len(state.queues.pending_edit),
        "leaf_update": len(state.queues.leaf_update),
        "foldernode_mechanical_update": len(state.queues.foldernode_mechanical_update),
        "foldernode_ai_summary_update": len(state.queues.foldernode_ai_summary_update),
        "retry": len(state.queues.retry),
        "failed": len(state.queues.failed),
        "deleted": len(state.queues.deleted),
        "move_or_rename": len(state.queues.move_or_rename),
    }
    return DiagnosticRepository(
        repository_ref=f"repository-{number}",
        manifest_node_count=len(state.nodes),
        node_state_counts=dict(sorted(node_states.items())),
        queue_counts=queues,
        last_scan_status=_safe_code(state.scan.last_scan_status),
        recent_runs=_recent_runs(index),
    )


def build_diagnostic_bundle(index_repositories: list[Path]) -> DiagnosticBundle:
    return DiagnosticBundle(
        environment={
            "operating_system": platform.system(),
            "operating_system_release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        contracts={
            "global_schema": GLOBAL_SCHEMA_VERSION,
            "repository_schema": REPOSITORY_SCHEMA_VERSION,
            "search_schema": SEARCH_SCHEMA_VERSION,
            "plugin_api": PLUGIN_API_VERSION,
        },
        repositories=[
            _repository_diagnostic(index.resolve(), number)
            for number, index in enumerate(index_repositories, start=1)
        ],
    )


def _write_zip(output: Path, entries: dict[str, bytes]) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Diagnostic output already exists: {output}")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def create_diagnostic_bundle(output: Path, index_repositories: list[Path]) -> Path:
    bundle = build_diagnostic_bundle(index_repositories)
    rendered = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2).encode(
        "utf-8"
    )
    if len(rendered) > DIAGNOSTIC_MAX_JSON_BYTES:
        raise ValueError("Diagnostic payload exceeds the local size limit")
    return _write_zip(output, {DIAGNOSTIC_ENTRY: rendered + b"\n"})


def _read_entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not set(names) <= {DIAGNOSTIC_ENTRY, CONSENT_ENTRY}:
            raise ValueError("Diagnostic archive contains unexpected entries")
        entries: dict[str, bytes] = {}
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > DIAGNOSTIC_MAX_JSON_BYTES or info.flag_bits & 0x1:
                raise ValueError("Diagnostic archive entry is too large or encrypted")
            entries[name] = archive.read(name)
    if DIAGNOSTIC_ENTRY not in entries:
        raise ValueError("Diagnostic archive is missing diagnostics.json")
    return entries


def inspect_diagnostic_bundle(path: Path) -> tuple[DiagnosticBundle, DiagnosticConsent | None]:
    entries = _read_entries(path)
    try:
        bundle = DiagnosticBundle.model_validate_json(entries[DIAGNOSTIC_ENTRY])
        consent = (
            DiagnosticConsent.model_validate_json(entries[CONSENT_ENTRY])
            if CONSENT_ENTRY in entries
            else None
        )
    except ValueError as error:
        raise ValueError("Diagnostic archive contains invalid JSON or schema") from error
    return bundle, consent


def prepare_diagnostic_share(source: Path, output: Path, *, consent: bool) -> Path:
    if not consent:
        raise PermissionError("Explicit consent is required before preparing a shareable bundle")
    entries = _read_entries(source)
    if CONSENT_ENTRY in entries:
        raise ValueError("Diagnostic bundle already contains a consent receipt")
    DiagnosticBundle.model_validate_json(entries[DIAGNOSTIC_ENTRY])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    receipt = DiagnosticConsent(source_bundle_sha256=source_hash)
    entries[CONSENT_ENTRY] = (
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    return _write_zip(output, entries)


def diagnostic_summary(path: Path) -> dict[str, Any]:
    bundle, consent = inspect_diagnostic_bundle(path)
    return {
        "schema_version": bundle.schema_version,
        "product_version": bundle.product_version,
        "repository_count": len(bundle.repositories),
        "run_count": sum(len(item.recent_runs) for item in bundle.repositories),
        "contains_file_content": bundle.contains_file_content,
        "contains_paths": bundle.contains_paths,
        "share_consent_recorded": consent is not None,
    }
