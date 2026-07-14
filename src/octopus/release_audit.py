from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Literal

from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .api import API_CONTRACT_VERSION
from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION
from .migrations import GLOBAL_SCHEMA_VERSION, REPOSITORY_SCHEMA_VERSION
from .models import SchemaInfo
from .plugin_sdk import ALLOWED_PLUGIN_PERMISSIONS, PLUGIN_API_VERSION
from .search import SEARCH_REPORT_SCHEMA_VERSION, SEARCH_SCHEMA_VERSION


class ReleaseCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str
    status: Literal["pass", "fail"]
    detail: str


class ReleaseAuditReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str
    engineering_passed: bool
    checks: list[ReleaseCheck]
    external_gates_not_executed: list[str] = Field(default_factory=list)


def runtime_contracts() -> dict[str, Any]:
    return {
        "global_config_schema": GLOBAL_SCHEMA_VERSION,
        "repository_schema": REPOSITORY_SCHEMA_VERSION,
        "markdown_index_schema": SchemaInfo().octopus_schema,
        "local_api": API_CONTRACT_VERSION,
        "plugin_api": PLUGIN_API_VERSION,
        "plugin_permissions": sorted(ALLOWED_PLUGIN_PERMISSIONS),
        "search_cache_schema": SEARCH_SCHEMA_VERSION,
        "search_report_schema": SEARCH_REPORT_SCHEMA_VERSION,
        "diagnostic_bundle_schema": DIAGNOSTIC_SCHEMA_VERSION,
    }


def _check(checks: list[ReleaseCheck], check_id: str, condition: bool, detail: str) -> None:
    checks.append(
        ReleaseCheck(
            check_id=check_id,
            status="pass" if condition else "fail",
            detail=detail,
        )
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_release_blockers(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise ValueError("known issues register has an invalid schema")
    blockers: list[str] = []
    for item in payload["issues"]:
        if not isinstance(item, dict):
            raise ValueError("known issue is not an object")
        severity = str(item.get("severity", "")).upper()
        status = str(item.get("status", "")).casefold()
        if severity in {"P0", "P1"} and status not in {"closed", "resolved"}:
            blockers.append(str(item.get("id", "unknown")))
    return blockers


def _repository_checks(root: Path, expected_version: str) -> list[ReleaseCheck]:
    checks: list[ReleaseCheck] = []
    parsed = Version(expected_version)
    _check(checks, "product_version", __version__ == expected_version, __version__)
    _check(
        checks,
        "rc_version",
        parsed.pre is not None and parsed.pre[0] == "rc",
        expected_version,
    )
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _check(
        checks,
        "single_version_source",
        pyproject["tool"]["hatch"]["version"]["path"] == "src/octopus/__init__.py",
        "Hatch reads src/octopus/__init__.py",
    )
    installer = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    installer_match = re.search(r'^#define AppVersion "([^"]+)"$', installer, re.MULTILINE)
    _check(
        checks,
        "installer_version",
        installer_match is not None and installer_match.group(1) == expected_version,
        installer_match.group(1) if installer_match else "missing",
    )
    readme = (root / "README.md").read_text(encoding="utf-8")
    _check(
        checks,
        "readme_candidate_version",
        f"active candidate is `{expected_version}`" in readme,
        expected_version,
    )

    freeze_payload = json.loads(
        (root / "docs" / "product" / "contract-freeze-v1.json").read_text(encoding="utf-8")
    )
    frozen_contracts = freeze_payload.get("contracts") if isinstance(freeze_payload, dict) else None
    _check(
        checks,
        "contract_freeze",
        frozen_contracts == runtime_contracts(),
        "runtime contracts match contract-freeze-v1.json",
    )
    issues_payload = json.loads(
        (root / "docs" / "releases" / "v0.9-known-issues.json").read_text(encoding="utf-8")
    )
    blockers = _open_release_blockers(issues_payload)
    _check(
        checks,
        "no_open_p0_p1",
        not blockers,
        "none" if not blockers else ",".join(blockers),
    )
    required_documents = [
        root / "docs" / "product" / "COMPATIBILITY_MATRIX.md",
        root / "docs" / "user" / "DIAGNOSTICS_AND_RECOVERY.md",
        root / "docs" / "support" / "SUPPORT_POLICY.md",
        root / "docs" / "support" / "EMERGENCY_ROLLBACK.md",
        root / "docs" / "releases" / "v0.9.md",
        root / "CHANGELOG.md",
    ]
    missing = [str(path.relative_to(root)) for path in required_documents if not path.is_file()]
    _check(
        checks,
        "required_release_documents",
        not missing,
        "complete" if not missing else ",".join(missing),
    )
    return checks


def _artifact_checks(
    directory: Path, expected_version: str, repository_root: Path
) -> list[ReleaseCheck]:
    checks: list[ReleaseCheck] = []
    files = [path for path in directory.iterdir() if path.is_file()]
    wheel = [path for path in files if path.suffix == ".whl" and expected_version in path.name]
    sdist = [
        path
        for path in files
        if path.name.endswith(".tar.gz") and expected_version in path.name
    ]
    _check(checks, "wheel_artifact", len(wheel) == 1, str(len(wheel)))
    _check(checks, "sdist_artifact", len(sdist) == 1, str(len(sdist)))

    manifest_path = directory / "build-manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    _check(
        checks,
        "build_manifest_version",
        isinstance(manifest, dict) and manifest.get("version") == expected_version,
        str(manifest.get("version", "missing")) if isinstance(manifest, dict) else "invalid",
    )
    current_commit = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _check(
        checks,
        "build_manifest_commit",
        isinstance(manifest, dict) and manifest.get("git_commit") == current_commit,
        current_commit,
    )
    _check(
        checks,
        "build_worktree_clean",
        isinstance(manifest, dict) and manifest.get("git_worktree_clean") is True,
        str(manifest.get("git_worktree_clean", "missing")),
    )
    checksum_path = directory / "SHA256SUMS.txt"
    checksum_entries: dict[str, str] = {}
    if checksum_path.is_file():
        for line in checksum_path.read_text(encoding="ascii").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64}) \*(.+)", line)
            if match:
                checksum_entries[match.group(2)] = match.group(1)
    artifacts = [path for path in files if path.name != checksum_path.name]
    checksum_valid = bool(artifacts) and set(checksum_entries) == {path.name for path in artifacts}
    if checksum_valid:
        checksum_valid = all(_sha256(path) == checksum_entries[path.name] for path in artifacts)
    _check(checks, "artifact_checksums", checksum_valid, f"{len(checksum_entries)} entries")
    return checks


def audit_release(
    root: Path,
    expected_version: str,
    *,
    artifact_directory: Path | None = None,
) -> ReleaseAuditReport:
    root = root.resolve()
    checks = _repository_checks(root, expected_version)
    if artifact_directory is not None:
        checks.extend(
            _artifact_checks(artifact_directory.resolve(), expected_version, root)
        )
    external = [
        "Authenticode certificate and RFC 3161 timestamp verification",
        "clean-VM install, upgrade, rollback, uninstall, and Defender exercise",
        "two historical signed RC installer rehearsals",
        "named human release/support owner confirmation",
    ]
    return ReleaseAuditReport(
        expected_version=expected_version,
        engineering_passed=all(item.status == "pass" for item in checks),
        checks=checks,
        external_gates_not_executed=external,
    )
