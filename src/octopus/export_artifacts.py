from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field

from .config import load_global_config
from .models import OctopusModel, utc_now
from .utils import atomic_write_json, load_json, sha256_file

ARTIFACT_TTL_HOURS = 24
_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")


class ExportArtifact(OctopusModel):
    artifact_id: str
    workspace_id: str
    file_name: str
    size_bytes: int
    sha256: str
    created_at: str
    expires_at: str
    included_source_count: int = 0
    skipped_source_count: int = 0
    warnings: list[str] = Field(default_factory=list)


def _artifact_root(workspace_id: str) -> Path:
    workspace = load_global_config().workspaces.get(workspace_id)
    if workspace is None:
        raise FileNotFoundError("Workspace not found")
    root = Path(workspace.storage_path).expanduser().resolve() / "exports" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _manifest_summary(path: Path) -> tuple[int, int, list[str]]:
    included = 0
    skipped = 0
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            payload = json.loads(archive.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return 0, 0, ["研究包清单无法读取。"]
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return 0, 0, ["研究包清单格式无效。"]
    pending = changed = unavailable = 0
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("included_source")):
            included += 1
        else:
            skipped += 1
        if raw.get("review_state") != "confirmed":
            pending += 1
        if raw.get("freshness_status") not in {None, "", "current"}:
            changed += 1
        if raw.get("source_status") != "resolved":
            unavailable += 1
    if pending:
        warnings.append(f"{pending} 条证据尚未人工核验。")
    if changed:
        warnings.append(f"{changed} 条来源状态需要复核。")
    if unavailable:
        warnings.append(f"{unavailable} 条来源当前不可访问。")
    return included, skipped, warnings


def cleanup_export_artifacts(workspace_id: str) -> None:
    root = _artifact_root(workspace_id)
    now = datetime.now(UTC)
    for record_path in root.glob("*.json"):
        payload = load_json(record_path, {})
        try:
            expires = datetime.fromisoformat(str(payload.get("expires_at", "")))
        except (TypeError, ValueError):
            expires = datetime.fromtimestamp(0, tz=UTC)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires > now:
            continue
        record_path.unlink(missing_ok=True)
        record_path.with_suffix(".zip").unlink(missing_ok=True)


def register_export_artifact(
    workspace_id: str,
    source: Path,
    *,
    file_name: str | None = None,
) -> ExportArtifact:
    root = _artifact_root(workspace_id)
    cleanup_export_artifacts(workspace_id)
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".zip":
        raise FileNotFoundError("Export bundle is unavailable")
    artifact_id = uuid.uuid4().hex
    destination = root / f"{artifact_id}.zip"
    shutil.copy2(source, destination)
    included, skipped, warnings = _manifest_summary(destination)
    created = datetime.now(UTC)
    artifact = ExportArtifact(
        artifact_id=artifact_id,
        workspace_id=workspace_id,
        file_name=file_name or source.name,
        size_bytes=destination.stat().st_size,
        sha256=sha256_file(destination),
        created_at=utc_now(),
        expires_at=(created + timedelta(hours=ARTIFACT_TTL_HOURS)).isoformat(),
        included_source_count=included,
        skipped_source_count=skipped,
        warnings=warnings,
    )
    atomic_write_json(root / f"{artifact_id}.json", artifact.model_dump(mode="json"))
    return artifact


def resolve_export_artifact(workspace_id: str, artifact_id: str) -> tuple[ExportArtifact, Path]:
    if not _ARTIFACT_ID.fullmatch(artifact_id):
        raise FileNotFoundError("Export artifact not found")
    root = _artifact_root(workspace_id)
    payload = load_json(root / f"{artifact_id}.json")
    if not isinstance(payload, dict):
        raise FileNotFoundError("Export artifact not found")
    artifact = ExportArtifact.model_validate(payload)
    if artifact.workspace_id != workspace_id:
        raise FileNotFoundError("Export artifact not found")
    expires = datetime.fromisoformat(artifact.expires_at)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    path = root / f"{artifact_id}.zip"
    if expires <= datetime.now(UTC) or not path.is_file():
        path.unlink(missing_ok=True)
        (root / f"{artifact_id}.json").unlink(missing_ok=True)
        raise FileNotFoundError("Export artifact expired")
    if path.stat().st_size != artifact.size_bytes or sha256_file(path) != artifact.sha256:
        raise ValueError("Export artifact integrity check failed")
    return artifact, path
