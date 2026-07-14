from __future__ import annotations

from pathlib import Path

import pytest

from octopus.config import create_repository, repository_config_path
from octopus.models import RepositoryConfig
from octopus.utils import atomic_write_json


@pytest.fixture
def repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, RepositoryConfig]:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    raw = tmp_path / "raw"
    index = tmp_path / "index"
    raw.mkdir()
    config = create_repository(raw, index, "Test Repository")
    config.stability.minimum_quiet_seconds = 0
    config.stability.required_stable_scan_count = 1
    config.ai_policy.enabled = False
    atomic_write_json(repository_config_path(index), config.model_dump(mode="json", by_alias=True))
    return raw, index, config


@pytest.fixture(autouse=True)
def clean_python_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.delenv("PYTHONINSPECT", raising=False)
