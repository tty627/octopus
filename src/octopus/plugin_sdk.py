from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from urllib.parse import urlparse
from urllib.request import url2pathname

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import load_repository_config, load_repository_state
from .runtime import octopus_command
from .search import SearchIndex
from .utils import atomic_write_json, atomic_write_text, load_json

PLUGIN_API_VERSION = "1.0"
PLUGIN_MANIFEST_SCHEMA_VERSION = "1.0"
PLUGIN_LOG_LIMIT = 4_000
PLUGIN_TEXT_EXPORT_LIMIT = 1_000_000
PluginPermission = Literal[
    "index.query",
    "index.timeline",
    "export.write",
    "export.copy_confirmed",
]
ALLOWED_PLUGIN_PERMISSIONS = {
    "index.query",
    "index.timeline",
    "export.write",
    "export.copy_confirmed",
}


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    plugin_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{2,79}$")
    name: str = Field(min_length=1, max_length=120)
    version: str
    plugin_api: str
    entrypoint: str
    description: str = ""
    permissions: list[PluginPermission] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manifest(self) -> PluginManifest:
        Version(self.version)
        try:
            SpecifierSet(self.plugin_api)
        except InvalidSpecifier as error:
            raise ValueError("plugin_api must be a valid version specifier") from error
        entrypoint = PurePosixPath(self.entrypoint.replace("\\", "/"))
        if entrypoint.is_absolute() or ".." in entrypoint.parts or entrypoint.suffix != ".py":
            raise ValueError("entrypoint must be a relative Python file")
        if len(self.permissions) != len(set(self.permissions)):
            raise ValueError("plugin permissions must be unique")
        return self


class PluginOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["export_text", "copy_source"]
    path: str
    content: str = ""
    node_id: str = ""


class PluginResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    operations: list[PluginOperation] = Field(default_factory=list, max_length=1_000)


class PluginRunReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    plugin_id: str
    plugin_version: str
    plugin_api_version: str = PLUGIN_API_VERSION
    status: Literal["success", "failed"]
    granted_permissions: list[str]
    exported_files: list[str] = Field(default_factory=list)
    copied_node_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    log: str = ""
    error_code: str = ""


class PluginCompatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compatible: bool
    plugin_api_version: str = PLUGIN_API_VERSION
    requested_range: str
    missing_permissions: list[str] = Field(default_factory=list)
    error_code: str = ""


def reference_plugins_directory() -> Path:
    packaged = Path(__file__).resolve().parent / "reference_plugins"
    if packaged.exists():
        return packaged
    return Path(__file__).resolve().parents[2] / "plugins"


def load_plugin_manifest(path: Path) -> tuple[Path, PluginManifest]:
    manifest_path = path / "plugin.json" if path.is_dir() else path
    root = manifest_path.parent.resolve()
    payload = load_json(manifest_path)
    manifest = PluginManifest.model_validate(payload)
    entrypoint = root.joinpath(*PurePosixPath(manifest.entrypoint.replace("\\", "/")).parts)
    if not entrypoint.is_file() or root not in entrypoint.resolve().parents:
        raise ValueError("plugin entrypoint is missing or outside the plugin directory")
    return root, manifest


def check_plugin_compatibility(
    manifest: PluginManifest,
    granted_permissions: set[str] | None = None,
) -> PluginCompatibility:
    granted = granted_permissions or set()
    try:
        compatible = Version(PLUGIN_API_VERSION) in SpecifierSet(manifest.plugin_api)
    except InvalidSpecifier:
        compatible = False
    missing: list[str] = sorted(set(manifest.permissions) - granted)
    error_code = "" if compatible and not missing else (
        "incompatible_plugin_api" if not compatible else "permission_not_granted"
    )
    return PluginCompatibility(
        compatible=compatible and not missing,
        requested_range=manifest.plugin_api,
        missing_permissions=missing,
        error_code=error_code,
    )


def discover_plugins(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    discovered: list[dict[str, Any]] = []
    for manifest_path in sorted(directory.glob("*/plugin.json")):
        try:
            _, manifest = load_plugin_manifest(manifest_path)
            compatibility = check_plugin_compatibility(
                manifest, set(manifest.permissions)
            )
            discovered.append(
                {
                    **manifest.model_dump(mode="json"),
                    "compatible": compatibility.compatible,
                    "error_code": compatibility.error_code,
                }
            )
        except (OSError, ValueError) as error:
            discovered.append(
                {
                    "plugin_id": manifest_path.parent.name,
                    "compatible": False,
                    "error_code": type(error).__name__,
                }
            )
    return discovered


def _sanitized_search_results(
    index: Path, query: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    results = SearchIndex(index).search_report(query, limit=100, mode="local").results
    sanitized: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    for result in results:
        sanitized.append(
            {
                "node_id": result.node_id,
                "name": result.name,
                "index_type": result.index_type,
                "summary": result.summary,
                "status": result.status,
                "risk_flags": result.risk_flags,
                "evidence": [
                    {
                        "locator": item.locator,
                        "kind": item.kind,
                        "text_excerpt": item.text_excerpt,
                    }
                    for item in result.evidence[:5]
                ],
            }
        )
        if result.source_uri:
            sources[result.node_id] = result.source_uri
    return sanitized, sources


def _timeline_signals(index: Path) -> list[dict[str, str]]:
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    signals: list[dict[str, str]] = []
    for node in state.nodes.values():
        name = Path(node.raw_relative_path).name or config.repository.repository_name
        signals.append(
            {
                "node_id": node.node_id,
                "name": name,
                "kind": node.node_kind,
                "status": node.state.value,
                "modified_at": node.fingerprint.modified_at,
            }
        )
    return sorted(signals, key=lambda item: (item["modified_at"], item["name"]))


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise ValueError(f"unsafe plugin export path: {value}")
    return path


def _source_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("plugin copy source must use a local file URI")
    value = url2pathname(parsed.path)
    if parsed.netloc:
        value = f"//{parsed.netloc}{value}"
    return Path(value).resolve()


def _sanitized_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _sanitize_log(text: str, replacements: list[Path]) -> str:
    value = text
    for path in replacements:
        value = value.replace(str(path), f"<{path.name or 'path'}>")
    value = re.sub(
        r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        value,
    )
    value = re.sub(
        r"(?i)(?:[a-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*",
        "<path>",
        value,
    )
    return value[-PLUGIN_LOG_LIMIT:]


def _apply_operations(
    response: PluginResponse,
    export_directory: Path,
    permissions: set[str],
    confirmed_node_ids: set[str],
    source_uris: dict[str, str],
) -> tuple[list[str], list[str]]:
    planned: list[tuple[PluginOperation, PurePosixPath, Path, Path | None]] = []
    seen_destinations: set[Path] = set()
    # Validate the complete declarative plan before performing the first write. This
    # keeps rejected permission/confirmation requests from leaving partial exports.
    for operation in response.operations:
        relative = _safe_relative_path(operation.path)
        destination = export_directory.joinpath(*relative.parts).resolve()
        if export_directory != destination and export_directory not in destination.parents:
            raise ValueError("plugin export escaped the authorized directory")
        if destination in seen_destinations:
            raise ValueError(f"plugin export path is duplicated: {relative.as_posix()}")
        seen_destinations.add(destination)
        if destination.exists():
            raise FileExistsError(f"plugin export already exists: {relative.as_posix()}")
        source: Path | None = None
        if operation.operation == "export_text":
            if "export.write" not in permissions:
                raise PermissionError("plugin lacks export.write")
            if len(operation.content.encode("utf-8")) > PLUGIN_TEXT_EXPORT_LIMIT:
                raise ValueError("plugin text export exceeds the size limit")
        else:
            if "export.copy_confirmed" not in permissions:
                raise PermissionError("plugin lacks export.copy_confirmed")
            if not operation.node_id or operation.node_id not in confirmed_node_ids:
                raise PermissionError("plugin attempted to copy an unconfirmed node")
            source_uri = source_uris.get(operation.node_id)
            if not source_uri:
                raise ValueError("confirmed node has no local source")
            source = _source_path(source_uri)
            if not source.is_file():
                raise FileNotFoundError("confirmed plugin source is unavailable")
        planned.append((operation, relative, destination, source))

    exported: list[str] = []
    copied: list[str] = []
    for operation, relative, destination, source in planned:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if operation.operation == "export_text":
            atomic_write_text(destination, operation.content)
            exported.append(relative.as_posix())
            continue
        if source is None:  # pragma: no cover - guarded by the preflight above
            raise RuntimeError("copy source preflight was not completed")
        shutil.copy2(source, destination)
        exported.append(relative.as_posix())
        copied.append(operation.node_id)
    return exported, copied


def run_plugin(
    plugin_path: Path,
    index_repository: Path,
    export_directory: Path,
    *,
    granted_permissions: set[str],
    query: str = "",
    confirmed_node_ids: set[str] | None = None,
    timeout_seconds: float = 30.0,
) -> PluginRunReport:
    root, manifest = load_plugin_manifest(plugin_path)
    if not granted_permissions <= ALLOWED_PLUGIN_PERMISSIONS:
        raise PermissionError("one or more requested grants are not supported")
    compatibility = check_plugin_compatibility(manifest, granted_permissions)
    if not compatibility.compatible:
        raise PermissionError(
            compatibility.error_code + ":" + ",".join(compatibility.missing_permissions)
        )
    confirmed = confirmed_node_ids or set()
    resources: dict[str, Any] = {}
    source_uris: dict[str, str] = {}
    if "index.query" in manifest.permissions:
        if not query.strip():
            raise ValueError("index.query plugins require a query")
        resources["search_results"], source_uris = _sanitized_search_results(
            index_repository, query
        )
        resources["confirmed_node_ids"] = sorted(confirmed)
    if "index.timeline" in manifest.permissions:
        resources["timeline_signals"] = _timeline_signals(index_repository)
    invocation_id = uuid.uuid4().hex
    request = {
        "plugin_api_version": PLUGIN_API_VERSION,
        "invocation_id": invocation_id,
        "plugin": {
            "plugin_id": manifest.plugin_id,
            "version": manifest.version,
        },
        "resources": resources,
    }
    export = export_directory.resolve()
    export.mkdir(parents=True, exist_ok=True)
    if any(export.iterdir()):
        raise FileExistsError("plugin export directory must be empty")
    with tempfile.TemporaryDirectory(prefix="octopus-plugin-") as temporary:
        workspace = Path(temporary)
        request_path = workspace / "request.json"
        response_path = workspace / "response.json"
        atomic_write_json(request_path, request)
        command = octopus_command(
            "_plugin-worker",
            "--plugin",
            str(root),
            "--request",
            str(request_path),
            "--response",
            str(response_path),
        )
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=_sanitized_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                ),
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("plugin_timeout") from error
        config = load_repository_config(index_repository)
        private_paths = [
            root,
            export,
            workspace,
            index_repository.resolve(),
            Path(config.repository.raw_repository_path).resolve(),
        ]
        log = _sanitize_log(completed.stdout + completed.stderr, private_paths)
        if completed.returncode != 0 or not response_path.exists():
            raise RuntimeError(f"plugin_failed:{completed.returncode}:{log}")
        response = PluginResponse.model_validate(load_json(response_path))
        exported, copied = _apply_operations(
            response,
            export,
            granted_permissions,
            confirmed,
            source_uris,
        )
    return PluginRunReport(
        invocation_id=invocation_id,
        plugin_id=manifest.plugin_id,
        plugin_version=manifest.version,
        status="success",
        granted_permissions=sorted(granted_permissions),
        exported_files=exported,
        copied_node_ids=copied,
        summary=_sanitize_log(response.summary, private_paths),
        log=log,
    )
