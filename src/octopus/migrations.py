from __future__ import annotations

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


class MigrationReport(OctopusModel):
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = Field(default_factory=utc_now)
    dry_run: bool = True
    targets: list[MigrationTarget] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def required(self) -> bool:
        return bool(self.targets)


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
        prepared.append((target, path, backup, _migrate_payload(target, payload)))
    for _, path, backup, _ in prepared:
        shutil.copy2(path, backup)
    report_path = global_config_path().parent / "migrations" / applied.run_id / "report.json"
    try:
        for _, path, _, migrated_payload in prepared:
            atomic_write_json(path, migrated_payload)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(report_path, applied.model_dump(mode="json"))
    except Exception:
        for _, path, backup, _ in reversed(prepared):
            if backup.exists():
                atomic_write_json(path, load_json(backup))
        report_path.unlink(missing_ok=True)
        raise
    return applied


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
