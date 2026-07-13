from __future__ import annotations

from pathlib import Path

import pytest

from octopus.config import global_config_path, load_global_config
from octopus.migrations import apply_migrations, plan_migrations
from octopus.utils import atomic_write_json, load_json


def test_global_v01_migration_is_planned_backed_up_and_idempotent(
    repository: tuple[Path, Path, object],
) -> None:
    _, index, config = repository
    path = global_config_path()
    current = load_json(path)
    current["schema_version"] = "0.1"
    current.pop("service", None)
    for item in current["repositories"].values():
        item.pop("enabled", None)
    atomic_write_json(path, current)

    planned = plan_migrations([index])
    assert planned.dry_run
    assert len(planned.targets) == 1
    assert planned.targets[0].from_version == "0.1"
    applied = apply_migrations(planned)

    assert not applied.dry_run
    assert Path(applied.targets[0].backup_path).exists()
    assert load_global_config().schema_version == "0.2"
    assert load_global_config().service.host == "127.0.0.1"
    assert load_global_config().repositories[config.repository.raw_repo_id].enabled
    assert not plan_migrations([index]).required
    with pytest.raises(ValueError, match="already been applied"):
        apply_migrations(applied)


def test_migration_rejects_newer_or_changed_schema(
    repository: tuple[Path, Path, object],
) -> None:
    _, _, _ = repository
    path = global_config_path()
    payload = load_json(path)
    payload["schema_version"] = "9.0"
    atomic_write_json(path, payload)
    with pytest.raises(ValueError, match="newer schema"):
        plan_migrations([])


def test_migration_rolls_back_when_report_commit_fails(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _ = repository
    path = global_config_path()
    payload = load_json(path)
    payload["schema_version"] = "0.1"
    payload.pop("service", None)
    atomic_write_json(path, payload)
    planned = plan_migrations([])
    real_write = atomic_write_json

    def fail_report(target: Path, value: object) -> None:
        if target.name == "report.json":
            raise OSError("injected report failure")
        real_write(target, value)

    monkeypatch.setattr("octopus.migrations.atomic_write_json", fail_report)
    with pytest.raises(OSError, match="injected report failure"):
        apply_migrations(planned)
    assert load_json(path)["schema_version"] == "0.1"
