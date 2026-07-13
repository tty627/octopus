from __future__ import annotations

import time
from pathlib import Path

import pytest

from octopus.config import repository_config_path
from octopus.models import JobStatus, RepositoryConfig
from octopus.service_control import ensure_service_token, service_token_path, validate_loopback_host
from octopus.service_runtime import JobManager, RepositoryScheduler
from octopus.utils import atomic_write_json


def _wait(manager: JobManager, job_id: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job and job.status in {JobStatus.succeeded, JobStatus.failed}:
            return
        time.sleep(0.01)
    raise AssertionError("job did not complete")


def test_job_manager_tracks_success_failure_and_active_state() -> None:
    manager = JobManager(max_workers=1, max_retained_jobs=2)
    success = manager.submit("repo", "validate", lambda: {"ok": True})
    _wait(manager, success.job_id)
    assert manager.get(success.job_id).result == {"ok": True}  # type: ignore[union-attr]

    def fail() -> dict[str, object]:
        raise ValueError("sanitized failure")

    failed = manager.submit("repo", "validate", fail)
    _wait(manager, failed.job_id)
    result = manager.get(failed.job_id)
    assert result and result.status == JobStatus.failed
    assert result.error_code == "ValueError"
    assert not manager.active("repo")
    manager.shutdown()


def test_scheduler_submits_enabled_repositories_once_per_interval(
    repository: tuple[Path, Path, RepositoryConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, index, config = repository
    config.watcher.enabled = True
    config.watcher.initial_scan_on_startup = True
    config.watcher.scan_interval_minutes = 1
    atomic_write_json(repository_config_path(index), config.model_dump(mode="json", by_alias=True))
    calls: list[Path] = []
    monkeypatch.setattr(
        "octopus.service_runtime.run_repository_update",
        lambda path: calls.append(path) or {"ok": True},
    )
    clock = [0.0]
    manager = JobManager(max_workers=1)
    scheduler = RepositoryScheduler(manager, monotonic=lambda: clock[0])
    assert scheduler.run_once() == [config.repository.raw_repo_id]
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls == [index]
    assert scheduler.run_once() == []
    clock[0] = 61.0
    assert scheduler.run_once() == [config.repository.raw_repo_id]
    manager.shutdown()


def test_service_token_and_loopback_policy(repository: tuple[Path, Path, object]) -> None:
    token = ensure_service_token()
    assert len(token) >= 32
    assert ensure_service_token() == token
    assert service_token_path().read_text(encoding="utf-8").strip() == token
    assert validate_loopback_host("localhost") == "127.0.0.1"
    assert validate_loopback_host("::1") == "::1"
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_host("0.0.0.0")
    with pytest.raises(ValueError, match="numeric"):
        validate_loopback_host("example.com")
