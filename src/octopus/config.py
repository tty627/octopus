from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import (
    GlobalConfig,
    GlobalRepository,
    ManifestRepository,
    RepositoryConfig,
    RepositoryIdentity,
    RepositoryState,
)
from .utils import atomic_write_json, load_json

_GLOBAL_CONFIG_LOCK = threading.RLock()


@contextmanager
def global_config_lock() -> Iterator[None]:
    """Serialize in-process global-config read/modify/write transactions."""
    with _GLOBAL_CONFIG_LOCK:
        yield


def global_config_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "Octopus" / "config.json"


def local_data_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "Octopus"


def workspace_storage_path(workspace_id: str) -> Path:
    return local_data_root() / "workspaces" / workspace_id


def workspace_tasks_path(workspace_id: str) -> Path:
    return global_config_path().parent / "workspaces" / workspace_id / "tasks"


def runtime_jobs_path() -> Path:
    return local_data_root() / "runtime-jobs.json"


def octopus_dir(index_repository: Path) -> Path:
    return index_repository / ".octopus"


def repository_config_path(index_repository: Path) -> Path:
    return octopus_dir(index_repository) / "repository-config.json"


def repository_state_path(index_repository: Path) -> Path:
    return octopus_dir(index_repository) / "repository-state.json"


def load_global_config() -> GlobalConfig:
    with _GLOBAL_CONFIG_LOCK:
        raw = load_json(global_config_path(), {})
        return GlobalConfig.model_validate(raw or {})


def save_global_config(config: GlobalConfig) -> None:
    with _GLOBAL_CONFIG_LOCK:
        atomic_write_json(global_config_path(), config.model_dump(mode="json"))


def validate_repository_paths(raw: Path, index: Path) -> tuple[Path, Path]:
    raw = raw.expanduser().resolve()
    index = index.expanduser().resolve()
    if not raw.exists() or not raw.is_dir():
        raise ValueError(f"Raw Repository does not exist or is not a directory: {raw}")
    if raw == index or raw in index.parents or index in raw.parents:
        raise ValueError("Raw Repository and Index Repository must be separate, non-nested paths")
    return raw, index


def create_repository(
    raw: Path,
    index: Path,
    name: str | None = None,
    *,
    ai_enabled: bool | None = None,
    require_empty: bool = False,
) -> RepositoryConfig:
    raw, index = validate_repository_paths(raw, index)
    index_existed = index.exists()
    if index_existed and not index.is_dir():
        raise ValueError(f"Index Repository is not a directory: {index}")
    if require_empty and index_existed and any(index.iterdir()):
        raise ValueError(f"Index Repository must be empty: {index}")
    if repository_config_path(index).exists() or repository_state_path(index).exists():
        raise ValueError(f"Index Repository is already an Octopus repository: {index}")
    index.mkdir(parents=True, exist_ok=True)
    repo_id = str(uuid.uuid4())
    config = RepositoryConfig(
        repository=RepositoryIdentity(
            raw_repo_id=repo_id,
            raw_repository_path=str(raw),
            index_repository_path=str(index),
            repository_name=name or raw.name or "Octopus Repository",
        )
    )
    if ai_enabled is not None:
        config.ai_policy.enabled = ai_enabled
    state = RepositoryState(
        repository=ManifestRepository(
            raw_repo_id=repo_id,
            raw_repository_path_snapshot=str(raw),
            index_repository_path_snapshot=str(index),
        )
    )
    config_path = repository_config_path(index)
    state_path = repository_state_path(index)
    try:
        atomic_write_json(config_path, config.model_dump(mode="json", by_alias=True))
        atomic_write_json(state_path, state.model_dump(mode="json", by_alias=True))
        with global_config_lock():
            global_config = load_global_config()
            global_config.repositories[repo_id] = GlobalRepository(
                raw_repo_id=repo_id,
                name=config.repository.repository_name,
                index_repository_path=str(index),
            )
            global_config.active_repository_id = repo_id
            save_global_config(global_config)
    except Exception:
        config_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        octopus = octopus_dir(index)
        try:
            octopus.rmdir()
            if not index_existed:
                index.rmdir()
        except OSError:
            pass
        raise
    return config


def load_repository_config(index: Path) -> RepositoryConfig:
    raw = load_json(repository_config_path(index))
    if raw is None:
        raise FileNotFoundError(f"Not an Octopus Index Repository: {index}")
    return RepositoryConfig.model_validate(raw)


def save_repository_config(index: Path, config: RepositoryConfig) -> None:
    atomic_write_json(
        repository_config_path(index),
        config.model_dump(mode="json", by_alias=True),
    )


def load_repository_state(index: Path, config: RepositoryConfig) -> RepositoryState:
    raw = load_json(repository_state_path(index))
    if raw is None:
        return RepositoryState(
            repository=ManifestRepository(
                raw_repo_id=config.repository.raw_repo_id,
                raw_repository_path_snapshot=config.repository.raw_repository_path,
                index_repository_path_snapshot=config.repository.index_repository_path,
            )
        )
    return RepositoryState.model_validate(raw)


def resolve_repository(value: str | Path | None = None) -> Path:
    if value is not None:
        candidate = Path(value).expanduser().resolve()
        if repository_config_path(candidate).exists():
            return candidate
        global_config = load_global_config()
        key = str(value)
        if key in global_config.repositories:
            return Path(global_config.repositories[key].index_repository_path)
        for repo_item in global_config.repositories.values():
            if repo_item.name == key:
                return Path(repo_item.index_repository_path)
        raise FileNotFoundError(f"Unknown repository: {value}")
    global_config = load_global_config()
    if not global_config.active_repository_id:
        raise FileNotFoundError("No active repository. Run 'octopus init' or 'octopus repo use'.")
    active_repo = global_config.repositories.get(global_config.active_repository_id)
    if active_repo is None:
        raise FileNotFoundError("The active repository is missing from global configuration")
    return Path(active_repo.index_repository_path)
