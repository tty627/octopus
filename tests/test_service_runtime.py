from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from octopus.config import repository_config_path
from octopus.models import JobStatus, RepositoryConfig, ServiceJob
from octopus.service_control import ensure_service_token, service_token_path, validate_loopback_host
from octopus.service_runtime import JobManager, RepositoryScheduler
from octopus.utils import atomic_write_json


def _wait(manager: JobManager, job_id: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job and job.status in {
            JobStatus.succeeded,
            JobStatus.failed,
            JobStatus.canceled,
            JobStatus.interrupted,
        }:
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


def test_job_manager_exposes_and_preserves_progress() -> None:
    manager = JobManager(max_workers=1)
    started = Event()
    release = Event()

    def execute(report: Callable[[dict[str, object]], None]) -> dict[str, object]:
        report(
            {
                "phase": "processing",
                "discovered": 3,
                "processed": 1,
                "current_page": 7,
                "page_count": 40,
                "pages_completed": 6,
                "ocr_pages_completed": 2,
                "extraction_stage": "ocr",
            }
        )
        started.set()
        assert release.wait(timeout=5)
        report(
            {
                "phase": "completed",
                "discovered": 3,
                "processed": 3,
                "current_page": 40,
                "page_count": 40,
                "pages_completed": 40,
                "ocr_pages_completed": 8,
                "extraction_stage": "page_complete",
            }
        )
        return {"ok": True}

    submitted = manager.submit_with_progress("workspace", "workspace_sync", execute)
    assert started.wait(timeout=5)
    running = manager.get(submitted.job_id)
    assert running is not None
    assert running.status == JobStatus.running
    assert running.result["progress"] == {
        "phase": "processing",
        "discovered": 3,
        "processed": 1,
        "current_page": 7,
        "page_count": 40,
        "pages_completed": 6,
        "ocr_pages_completed": 2,
        "extraction_stage": "ocr",
    }

    release.set()
    _wait(manager, submitted.job_id)
    completed = manager.get(submitted.job_id)
    assert completed is not None
    assert completed.result == {
        "ok": True,
        "progress": {
            "phase": "completed",
            "discovered": 3,
            "processed": 3,
            "current_page": 40,
            "page_count": 40,
            "pages_completed": 40,
            "ocr_pages_completed": 8,
            "extraction_stage": "page_complete",
        },
    }
    manager.shutdown()


def test_job_manager_submits_only_one_unique_progress_job_under_concurrency() -> None:
    manager = JobManager(max_workers=1)
    callers = 8
    barrier = Barrier(callers)
    release = Event()

    def execute(report: Callable[[dict[str, object]], None]) -> dict[str, object]:
        report({"phase": "processing"})
        assert release.wait(timeout=5)
        return {"ok": True}

    def submit() -> object:
        barrier.wait(timeout=5)
        return manager.submit_unique_with_progress(
            "workspace",
            "workspace_sync",
            execute,
        )

    with ThreadPoolExecutor(max_workers=callers) as executor:
        submitted = list(executor.map(lambda _: submit(), range(callers)))

    accepted = [job for job in submitted if job is not None]
    assert len(accepted) == 1
    assert manager.active("workspace", "workspace_sync")
    release.set()
    _wait(manager, accepted[0].job_id)
    manager.shutdown()


def test_job_manager_persists_jobs_and_marks_incomplete_work_interrupted(tmp_path: Path) -> None:
    storage = tmp_path / "runtime-jobs.json"
    atomic_write_json(
        storage,
        [
            ServiceJob(
                job_id="persisted",
                repository_id="workspace",
                kind="workspace_sync",
                status=JobStatus.running,
            ).model_dump(mode="json")
        ],
    )

    manager = JobManager(max_workers=1, storage_path=storage)
    restored = manager.get("persisted")
    assert restored is not None
    assert restored.status == JobStatus.interrupted
    assert restored.error_code == "service_restarted"
    manager.shutdown()


def test_job_manager_cancels_progress_jobs_cooperatively(tmp_path: Path) -> None:
    manager = JobManager(max_workers=1, storage_path=tmp_path / "jobs.json")
    started = Event()

    def execute(report: Callable[[dict[str, object]], None]) -> dict[str, object]:
        started.set()
        while True:
            report({"phase": "processing"})
            time.sleep(0.01)

    job = manager.submit_with_progress("workspace", "workspace_sync", execute)
    assert started.wait(timeout=5)
    canceled = manager.cancel(job.job_id)
    assert canceled is not None and canceled.cancel_requested
    _wait(manager, job.job_id)
    completed = manager.get(job.job_id)
    assert completed is not None
    assert completed.status == JobStatus.canceled
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
