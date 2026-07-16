from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any, Literal

from .config import load_global_config, load_repository_config, runtime_jobs_path
from .engine import UpdateEngine
from .models import JobStatus, ServiceJob, utc_now
from .utils import atomic_write_json, load_json

JobFunction = Callable[[], dict[str, Any]]
JobProgressCallback = Callable[[dict[str, Any]], None]
ProgressJobFunction = Callable[[JobProgressCallback], dict[str, Any]]
JobKind = Literal[
    "update",
    "rebuild_search",
    "validate",
    "package",
    "workspace_sync",
    "workspace_rebuild",
    "workspace_ai_index",
    "task_export",
]

TERMINAL_JOB_STATUSES = {
    JobStatus.succeeded,
    JobStatus.failed,
    JobStatus.canceled,
    JobStatus.interrupted,
}


class JobCancelledError(RuntimeError):
    pass


class JobManager:
    def __init__(
        self,
        max_workers: int = 2,
        max_retained_jobs: int = 500,
        storage_path: Path | None = None,
    ) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="octopus-job",
        )
        self.max_retained_jobs = max_retained_jobs
        self.jobs: dict[str, ServiceJob] = {}
        self.futures: dict[str, Future[dict[str, Any]]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.storage_path = storage_path or runtime_jobs_path()
        self._last_persisted_at = 0.0
        self._load_persisted_jobs()

    def _load_persisted_jobs(self) -> None:
        raw = load_json(self.storage_path, [])
        values = raw if isinstance(raw, list) else []
        changed = False
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                job = ServiceJob.model_validate(value)
            except ValueError:
                continue
            if job.status in {JobStatus.queued, JobStatus.running}:
                job.status = JobStatus.interrupted
                job.finished_at = utc_now()
                job.error_code = "service_restarted"
                job.error_message = "The local service stopped before this job completed."
                job.cancel_requested = False
                changed = True
            self.jobs[job.job_id] = job
        self._prune()
        if changed:
            self._persist(force=True)

    def _persist(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_persisted_at < 0.25:
            return
        atomic_write_json(
            self.storage_path,
            [
                job.model_dump(mode="json")
                for job in sorted(self.jobs.values(), key=lambda item: item.created_at)
            ],
        )
        self._last_persisted_at = now

    def submit(
        self,
        repository_id: str,
        kind: JobKind,
        function: JobFunction,
    ) -> ServiceJob:
        with self.lock:
            self._prune()
            job = ServiceJob(
                job_id=uuid.uuid4().hex,
                repository_id=repository_id,
                kind=kind,
            )
            self.jobs[job.job_id] = job
            self.cancel_events[job.job_id] = threading.Event()
            self.futures[job.job_id] = self.executor.submit(self._execute, job.job_id, function)
            self._persist(force=True)
            return job.model_copy(deep=True)

    def submit_with_progress(
        self,
        repository_id: str,
        kind: JobKind,
        function: ProgressJobFunction,
    ) -> ServiceJob:
        """Submit a job that can publish progress without changing existing callers."""
        with self.lock:
            self._prune()
            job = ServiceJob(
                job_id=uuid.uuid4().hex,
                repository_id=repository_id,
                kind=kind,
            )
            self.jobs[job.job_id] = job
            self.cancel_events[job.job_id] = threading.Event()
            self.futures[job.job_id] = self.executor.submit(
                self._execute_with_progress,
                job.job_id,
                function,
            )
            self._persist(force=True)
            return job.model_copy(deep=True)

    def submit_unique_with_progress(
        self,
        repository_id: str,
        kind: JobKind,
        function: ProgressJobFunction,
    ) -> ServiceJob | None:
        """Submit only when the same repository has no active job of this kind."""
        with self.lock:
            if self._active_locked(repository_id, kind):
                return None
            self._prune()
            job = ServiceJob(
                job_id=uuid.uuid4().hex,
                repository_id=repository_id,
                kind=kind,
            )
            self.jobs[job.job_id] = job
            self.cancel_events[job.job_id] = threading.Event()
            self.futures[job.job_id] = self.executor.submit(
                self._execute_with_progress,
                job.job_id,
                function,
            )
            self._persist(force=True)
            return job.model_copy(deep=True)

    def _execute_with_progress(
        self,
        job_id: str,
        function: ProgressJobFunction,
    ) -> dict[str, Any]:
        return self._execute(
            job_id,
            lambda: function(partial(self._record_progress, job_id)),
        )

    def _record_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.status in TERMINAL_JOB_STATUSES:
                return
            event = self.cancel_events.get(job_id)
            if job.cancel_requested or (event is not None and event.is_set()):
                raise JobCancelledError("Job cancellation was requested")
            job.result = {**job.result, "progress": dict(progress)}
            self._persist()

    def _execute(self, job_id: str, function: JobFunction) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            if job.cancel_requested:
                job.status = JobStatus.canceled
                job.error_code = "canceled"
                job.error_message = "Job was canceled before it started."
                job.finished_at = utc_now()
                self._persist(force=True)
                return {}
            job.status = JobStatus.running
            job.started_at = utc_now()
            self._persist(force=True)
        try:
            result = function()
            with self.lock:
                if job.cancel_requested:
                    raise JobCancelledError("Job cancellation was requested")
                job.status = JobStatus.succeeded
                progress = job.result.get("progress")
                job.result = dict(result)
                if progress is not None and "progress" not in job.result:
                    job.result["progress"] = progress
            return result
        except JobCancelledError as error:
            with self.lock:
                job.status = JobStatus.canceled
                job.error_code = "canceled"
                job.error_message = str(error)
            return {}
        except Exception as error:
            with self.lock:
                job.status = JobStatus.failed
                job.error_code = type(error).__name__
                job.error_message = str(error)[:500]
            raise
        finally:
            with self.lock:
                job.finished_at = utc_now()
                self._persist(force=True)

    def get(self, job_id: str) -> ServiceJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def list(self, repository_id: str | None = None) -> list[ServiceJob]:
        with self.lock:
            jobs = [
                job.model_copy(deep=True)
                for job in self.jobs.values()
                if repository_id is None or job.repository_id == repository_id
            ]
        return sorted(jobs, key=lambda item: item.created_at, reverse=True)

    def active(self, repository_id: str, kind: str | None = None) -> bool:
        with self.lock:
            return self._active_locked(repository_id, kind)

    def _active_locked(self, repository_id: str, kind: str | None = None) -> bool:
        return any(
            job.repository_id == repository_id
            and (kind is None or job.kind == kind)
            and job.status in {JobStatus.queued, JobStatus.running}
            for job in self.jobs.values()
        )

    def cancel(self, job_id: str) -> ServiceJob | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                return None
            if job.status in TERMINAL_JOB_STATUSES:
                return job.model_copy(deep=True)
            job.cancel_requested = True
            event = self.cancel_events.get(job_id)
            if event is not None:
                event.set()
            future = self.futures.get(job_id)
            if job.status == JobStatus.queued and future is not None and future.cancel():
                job.status = JobStatus.canceled
                job.error_code = "canceled"
                job.error_message = "Job was canceled before it started."
                job.finished_at = utc_now()
            self._persist(force=True)
            return job.model_copy(deep=True)

    def _prune(self) -> None:
        completed = [job for job in self.jobs.values() if job.status in TERMINAL_JOB_STATUSES]
        completed.sort(key=lambda item: item.finished_at)
        excess = max(0, len(self.jobs) - self.max_retained_jobs + 1)
        for job in completed[:excess]:
            self.jobs.pop(job.job_id, None)
            self.futures.pop(job.job_id, None)
            self.cancel_events.pop(job.job_id, None)

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)
        with self.lock:
            self._persist(force=True)


def run_repository_update(index: Path) -> dict[str, Any]:
    return asdict(UpdateEngine(index).run())


class RepositoryScheduler:
    def __init__(
        self,
        jobs: JobManager,
        tick_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jobs = jobs
        self.tick_seconds = tick_seconds
        self.monotonic = monotonic
        self.last_submitted: dict[str, float] = {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop,
            name="octopus-scheduler",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=max(5.0, self.tick_seconds * 2))

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(self.tick_seconds)

    def run_once(self) -> list[str]:
        now = self.monotonic()
        submitted: list[str] = []
        global_config = load_global_config()
        for repository_id, repository in global_config.repositories.items():
            if not repository.enabled:
                continue
            index = Path(repository.index_repository_path)
            try:
                config = load_repository_config(index)
            except (OSError, ValueError):
                continue
            if not config.watcher.enabled or self.jobs.active(repository_id, "update"):
                continue
            interval = max(1, config.watcher.scan_interval_minutes) * 60
            last = self.last_submitted.get(repository_id)
            if last is None and not config.watcher.initial_scan_on_startup:
                self.last_submitted[repository_id] = now
                continue
            if last is not None and now - last < interval:
                continue
            self.jobs.submit(
                repository_id,
                "update",
                partial(run_repository_update, index),
            )
            self.last_submitted[repository_id] = now
            submitted.append(repository_id)
        return submitted


class WorkspaceScheduler:
    def __init__(
        self,
        jobs: JobManager,
        tick_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jobs = jobs
        self.tick_seconds = tick_seconds
        self.monotonic = monotonic
        self.last_submitted: dict[str, float] = {}
        self.pending_since: dict[str, float] = {}
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def request_sync(self, workspace_id: str) -> None:
        self.pending_since.setdefault(workspace_id, self.monotonic())

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._loop,
            name="octopus-workspace-scheduler",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=max(5.0, self.tick_seconds * 2))

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()
            self.stop_event.wait(self.tick_seconds)

    def _submit(self, workspace_id: str) -> ServiceJob | None:
        from .workspace_tasks_v2 import migrate_legacy_tasks
        from .workspace_v2 import WorkspaceStore, get_workspace

        try:
            workspace = get_workspace(workspace_id)
        except KeyError:
            return None
        store = WorkspaceStore(workspace)

        def execute(progress: JobProgressCallback) -> dict[str, Any]:
            result = store.sync(progress)
            result["task_migration"] = migrate_legacy_tasks(workspace)
            return result

        return self.jobs.submit_unique_with_progress(
            workspace_id,
            "workspace_sync",
            execute,
        )

    def run_once(self) -> list[str]:
        now = self.monotonic()
        submitted: list[str] = []
        config = load_global_config()
        known_ids = set(config.workspaces)
        self.pending_since = {
            key: value for key, value in self.pending_since.items() if key in known_ids
        }
        for workspace_id, workspace in config.workspaces.items():
            policy = workspace.sync_policy
            if not workspace.enabled or not policy.auto_sync_enabled:
                continue
            last = self.last_submitted.get(workspace_id)
            pending = self.pending_since.get(workspace_id)
            event_due = pending is not None and now - pending >= policy.debounce_seconds
            reconciliation_due = (
                last is None or now - last >= max(1, policy.reconciliation_interval_minutes) * 60
            )
            if not event_due and not reconciliation_due:
                continue
            if self.jobs.active(workspace_id, "workspace_sync"):
                continue
            job = self._submit(workspace_id)
            if job is None:
                continue
            self.last_submitted[workspace_id] = now
            self.pending_since.pop(workspace_id, None)
            submitted.append(workspace_id)
        return submitted


class WorkspaceChangeMonitor:
    def __init__(
        self,
        on_change: Callable[[str], None],
        refresh_seconds: float = 30.0,
    ) -> None:
        self.on_change = on_change
        self.refresh_seconds = refresh_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.observer: Any = None
        self.watches: dict[str, tuple[str, Any]] = {}
        self.lock = threading.RLock()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        try:
            from watchdog.observers import Observer
        except ImportError:
            return
        self.observer = Observer()
        self.observer.start()
        self.stop_event.clear()
        self.refresh()
        self.thread = threading.Thread(
            target=self._loop,
            name="octopus-workspace-monitor",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=max(5.0, self.refresh_seconds * 2))
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
        self.watches.clear()

    def _loop(self) -> None:
        while not self.stop_event.wait(self.refresh_seconds):
            self.refresh()

    def refresh(self) -> None:
        if self.observer is None:
            return
        from watchdog.events import FileSystemEventHandler

        config = load_global_config()
        desired = {
            workspace_id: str(Path(workspace.raw_path).expanduser().resolve())
            for workspace_id, workspace in config.workspaces.items()
            if workspace.enabled
            and workspace.sync_policy.auto_sync_enabled
            and Path(workspace.raw_path).expanduser().is_dir()
        }
        with self.lock:
            for workspace_id, (path, watch) in list(self.watches.items()):
                if desired.get(workspace_id) == path:
                    continue
                self.observer.unschedule(watch)
                self.watches.pop(workspace_id, None)
            for workspace_id, path in desired.items():
                if workspace_id in self.watches:
                    continue
                callback = self.on_change

                class Handler(FileSystemEventHandler):
                    def on_any_event(
                        self,
                        event: Any,
                        *,
                        current_id: str = workspace_id,
                        current_callback: Callable[[str], None] = callback,
                    ) -> None:
                        if not getattr(event, "is_directory", False):
                            current_callback(current_id)

                watch = self.observer.schedule(Handler(), path, recursive=True)
                self.watches[workspace_id] = (path, watch)
