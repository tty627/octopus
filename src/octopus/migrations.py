from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, computed_field

from .config import global_config_path, repository_config_path, repository_state_path
from .models import GlobalConfig, OctopusModel, utc_now
from .utils import atomic_write_json, load_json

GLOBAL_SCHEMA_VERSION = "0.2"
REPOSITORY_SCHEMA_VERSION = "0.2"
MigrationKind = Literal["global_config", "repository_config", "repository_state"]


class MigrationTarget(OctopusModel):
    kind: MigrationKind
    path: str
    from_version: str
    to_version: str
    changes: list[str] = Field(default_factory=list)
    backup_path: str = ""
    before_sha256: str = ""
    after_sha256: str = ""
    backup_sha256: str = ""


class MigrationReport(OctopusModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = Field(default_factory=utc_now)
    dry_run: bool = True
    targets: list[MigrationTarget] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def required(self) -> bool:
        return bool(self.targets)


class MigrationRollbackReport(OctopusModel):
    run_id: str
    rolled_back_at: str = Field(default_factory=utc_now)
    restored_targets: list[MigrationKind] = Field(default_factory=list)
    status: Literal["rolled_back"] = "rolled_back"


def _migration_directory(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid migration run ID")
    return global_config_path().parent / "migrations" / run_id


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError as error:
        raise ValueError(f"Invalid Octopus schema version: {version!r}") from error


def _schema_version(payload: dict[str, Any], kind: str) -> str:
    if kind == "global_config":
        return str(payload.get("schema_version", "0.1"))
    schema = payload.get("schema", {})
    if not isinstance(schema, dict):
        raise ValueError(f"Invalid schema object in {kind}")
    return str(schema.get("octopus_schema", "0.1"))


def _plan_target(path: Path, kind: MigrationKind) -> MigrationTarget | None:
    payload = load_json(path)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"Migration target is not a JSON object: {path}")
    current = _schema_version(payload, kind)
    target = GLOBAL_SCHEMA_VERSION if kind == "global_config" else REPOSITORY_SCHEMA_VERSION
    if _version_tuple(current) > _version_tuple(target):
        raise ValueError(
            f"{path} uses newer schema {current}; this Octopus supports up to {target}"
        )
    if current == target:
        return None
    if kind == "global_config" and current == "0.1":
        return MigrationTarget(
            kind=kind,
            path=str(path),
            from_version=current,
            to_version=target,
            changes=[
                "add local service defaults",
                "add per-repository enabled flag",
            ],
        )
    raise ValueError(f"No migration path for {kind} schema {current} -> {target}")


def plan_migrations(index_repositories: list[Path] | None = None) -> MigrationReport:
    report = MigrationReport()
    global_target = _plan_target(global_config_path(), "global_config")
    if global_target:
        report.targets.append(global_target)
    for index in index_repositories or []:
        resolved = index.resolve()
        targets: list[tuple[Path, MigrationKind]] = [
            (repository_config_path(resolved), "repository_config"),
            (repository_state_path(resolved), "repository_state"),
        ]
        for path, kind in targets:
            target = _plan_target(path, kind)
            if target:
                report.targets.append(target)
    return report


def _migrate_payload(target: MigrationTarget, payload: dict[str, Any]) -> dict[str, Any]:
    if target.kind == "global_config" and target.from_version == "0.1":
        payload = dict(payload)
        payload["schema_version"] = GLOBAL_SCHEMA_VERSION
        return GlobalConfig.model_validate(payload).model_dump(mode="json")
    raise ValueError(
        f"No migration implementation for {target.kind} "
        f"{target.from_version} -> {target.to_version}"
    )


def apply_migrations(report: MigrationReport) -> MigrationReport:
    if not report.dry_run:
        raise ValueError("Migration report has already been applied")
    applied = report.model_copy(deep=True)
    applied.dry_run = False
    prepared: list[tuple[MigrationTarget, Path, Path, dict[str, Any]]] = []
    for target in applied.targets:
        path = Path(target.path)
        payload = load_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"Migration target changed or disappeared: {path}")
        current = _schema_version(payload, target.kind)
        if current != target.from_version:
            raise ValueError(
                f"Migration target changed from {target.from_version} to {current}: {path}"
            )
        backup = path.parent / "migrations" / applied.run_id / path.name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise FileExistsError(f"Migration backup already exists: {backup}")
        target.backup_path = str(backup)
        target.before_sha256 = _file_sha256(path)
        prepared.append((target, path, backup, _migrate_payload(target, payload)))
    for _, path, backup, _ in prepared:
        shutil.copy2(path, backup)
    for target, _, backup, _ in prepared:
        target.backup_sha256 = _file_sha256(backup)
        if target.backup_sha256 != target.before_sha256:
            raise OSError("Migration backup checksum mismatch")
    report_path = _migration_directory(applied.run_id) / "report.json"
    try:
        for target, path, _, migrated_payload in prepared:
            atomic_write_json(path, migrated_payload)
            target.after_sha256 = _file_sha256(path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(report_path, applied.model_dump(mode="json"))
    except Exception:
        for _, path, backup, _ in reversed(prepared):
            if backup.exists():
                atomic_write_json(path, load_json(backup))
        report_path.unlink(missing_ok=True)
        raise
    return applied


def _allowed_rollback_path(target: MigrationTarget) -> Path:
    path = Path(target.path).resolve()
    if target.kind == "global_config":
        if path != global_config_path().resolve():
            raise ValueError("Migration report contains an unexpected global config path")
        return path
    filename = {
        "repository_config": "repository-config.json",
        "repository_state": "repository-state.json",
    }[target.kind]
    repositories = GlobalConfig.model_validate(
        load_json(global_config_path(), {})
    ).repositories.values()
    registered = {
        (Path(item.index_repository_path).resolve() / ".octopus" / filename).resolve()
        for item in repositories
    }
    if path not in registered:
        raise ValueError("Migration report contains an unregistered repository path")
    return path


def rollback_migration(run_id: str) -> MigrationRollbackReport:
    directory = _migration_directory(run_id)
    report_path = directory / "report.json"
    rollback_path = directory / "rollback.json"
    if rollback_path.exists():
        raise ValueError("Migration has already been rolled back")
    payload = load_json(report_path)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"Applied migration report is unavailable: {run_id}")
    report = MigrationReport.model_validate(payload)
    if report.dry_run or not report.targets:
        raise ValueError("Only an applied migration can be rolled back")

    prepared: list[tuple[MigrationTarget, Path, dict[str, Any], dict[str, Any]]] = []
    for target in report.targets:
        path = _allowed_rollback_path(target)
        backup = Path(target.backup_path).resolve()
        expected_backup = directory / path.name
        if backup != expected_backup.resolve() or not backup.is_file():
            raise ValueError("Migration backup is missing or outside its run directory")
        if not target.backup_sha256 or _file_sha256(backup) != target.backup_sha256:
            raise ValueError("Migration backup checksum mismatch")
        if not path.is_file() or not target.after_sha256:
            raise ValueError("Migrated target is unavailable or has no committed checksum")
        if _file_sha256(path) != target.after_sha256:
            raise ValueError("Migrated target changed after migration; refusing to overwrite it")
        current = load_json(path)
        original = load_json(backup)
        if not isinstance(current, dict) or not isinstance(original, dict):
            raise ValueError("Migration rollback target is not a JSON object")
        prepared.append((target, path, current, original))

    rollback = MigrationRollbackReport(run_id=run_id)
    try:
        for target, path, _, original in reversed(prepared):
            atomic_write_json(path, original)
            if _file_sha256(path) != target.before_sha256:
                raise OSError("Restored migration target checksum mismatch")
            rollback.restored_targets.append(target.kind)
        atomic_write_json(rollback_path, rollback.model_dump(mode="json"))
    except Exception:
        for _, path, current, _ in prepared:
            atomic_write_json(path, current)
        rollback_path.unlink(missing_ok=True)
        raise
    return rollback


def migration_report_markdown(report: MigrationReport) -> str:
    lines = [f"# Octopus Migration {report.run_id}", ""]
    lines.append(f"- mode: {'dry-run' if report.dry_run else 'applied'}")
    lines.append(f"- targets: {len(report.targets)}")
    for target in report.targets:
        lines.extend(
            [
                "",
                f"## {target.kind}",
                "",
                f"- path: `{target.path}`",
                f"- version: {target.from_version} → {target.to_version}",
            ]
        )
        lines.extend(f"- change: {change}" for change in target.changes)
        if target.backup_path:
            lines.append(f"- backup: `{target.backup_path}`")
    return "\n".join(lines) + "\n"


def migration_rollback_markdown(report: MigrationRollbackReport) -> str:
    lines = [
        f"# Octopus Migration Rollback {report.run_id}",
        "",
        f"- status: {report.status}",
        f"- restored targets: {len(report.restored_targets)}",
    ]
    lines.extend(f"- restored: `{kind}`" for kind in report.restored_targets)
    return "\n".join(lines) + "\n"
