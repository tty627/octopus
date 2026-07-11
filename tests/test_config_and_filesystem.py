from __future__ import annotations

from pathlib import Path

import pytest

from octopus.config import (
    load_global_config,
    load_repository_config,
    resolve_repository,
    validate_repository_paths,
)
from octopus.filesystem import RawRepository, ensure_outside_raw


def test_repository_registration_and_resolution(repository: tuple[Path, Path, object]) -> None:
    raw, index, config = repository
    loaded = load_repository_config(index)
    assert loaded.repository.raw_repository_path == str(raw.resolve())
    assert resolve_repository() == index.resolve()
    assert resolve_repository(loaded.repository.raw_repo_id) == index.resolve()
    assert load_global_config().active_repository_id == loaded.repository.raw_repo_id


def test_rejects_nested_repository_paths(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    with pytest.raises(ValueError, match="non-nested"):
        validate_repository_paths(raw, raw / "index")


def test_read_only_boundary_prevents_escape_and_write(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    (raw / "note.txt").write_text("hello", encoding="utf-8")
    source = RawRepository(raw)
    assert source.read_text("note.txt") == "hello"
    with pytest.raises(PermissionError):
        source.resolve_relative("../outside.txt")
    with pytest.raises(PermissionError):
        ensure_outside_raw(raw / "generated.md", raw)
    ensure_outside_raw(index / "generated.md", raw)
