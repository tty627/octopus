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

from .config import load_global_config, load_repository_config
from .engine import UpdateEngine
from .models import JobStatus, ServiceJob, utc_now

JobFunction = Callable[[], dict[str, Any]]
JobProgressCallback = Callable[[dict[str, Any]], None]
ProgressJobFunction = Callable[[JobProgressCallback], dict[str, Any]]
JobKind = Literal["update", "rebuild_search", "validate", "package", "workspace_sync"]


class JobManager:
    def __init__(self, max_workers: int = 2, max_retained_jobs: int = 500) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="octopus-job",
        )
        self.max_retained_jobs = max_retained_jobs
        self.jobs: dict[str, ServiceJob] = {}
        self.futures: dict[str, Future[dict[str, Any]]] = {}
        self.lock = threading.RLock()

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
            self.futures[job.job_id] = self.executor.submit(self._execute, job.job_id, function)
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
            self.futures[job.job_id] = self.executor.submit(
                self._execute_with_progress,
                job.job_id,
                function,
            )
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
            self.futures[job.job_id] = self.executor.submit(
                self._execute_with_progress,
                job.job_id,
                function,
            )
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
            if job is None or job.status in {JobStatus.succeeded, JobStatus.failed}:
                return
            job.result = {**job.result, "progress": dict(progress)}

    def _execute(self, job_id: str, function: JobFunction) -> dict[str, Any]:
        with self.lock:
            job = self.jobs[job_id]
            job.status = JobStatus.running
            job.started_at = utc_now()
        try:
            result = function()
            with self.lock:
                job.status = JobStatus.succeeded
                progress = job.result.get("progress")
                job.result = dict(result)
                if progress is not None and "progress" not in job.result:
                    job.result["progress"] = progress
            return result
        except Exception as error:
            with self.lock:
                job.status = JobStatus.failed
                job.error_code = type(error).__name__
                job.error_message = str(error)[:500]
            raise
        finally:
            with self.lock:
                job.finished_at = utc_now()

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

    def _prune(self) -> None:
        completed = [
            job
            for job in self.jobs.values()
            if job.status in {JobStatus.succeeded, JobStatus.failed}
        ]
        completed.sort(key=lambda item: item.finished_at)
        excess = max(0, len(self.jobs) - self.max_retained_jobs + 1)
        for job in completed[:excess]:
            self.jobs.pop(job.job_id, None)
            self.futures.pop(job.job_id, None)

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)


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
