from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .config import load_global_config, load_repository_config, load_repository_state
from .engine import UpdateEngine
from .migrations import plan_migrations
from .models import ServiceJob
from .search import SearchIndex
from .service_control import ensure_service_token
from .service_runtime import JobManager, RepositoryScheduler
from .transactions import load_run_report
from .validation import validate_repository


class UpdateRequest(BaseModel):
    dry_run: bool = False
    scan_only: bool = False
    leaf_only: bool = False
    foldernode_only: bool = False
    retry_only: bool = False
    force_path: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    full: bool = False
    limit: int = Field(default=20, ge=1, le=100)


def _repository_path(repository_id: str) -> Path:
    global_config = load_global_config()
    repository = global_config.repositories.get(repository_id)
    if repository is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found")
    return Path(repository.index_repository_path)


def _repository_payload(repository_id: str, index: Path) -> dict[str, Any]:
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    counts: dict[str, int] = {}
    for node in state.nodes.values():
        counts[node.state.value] = counts.get(node.state.value, 0) + 1
    return {
        "repository_id": repository_id,
        "name": config.repository.repository_name,
        "raw_repository_path": config.repository.raw_repository_path,
        "index_repository_path": str(index),
        "scan": state.scan.model_dump(mode="json"),
        "states": counts,
        "queues": state.queues.model_dump(mode="json"),
    }


def create_app(
    *,
    token: str | None = None,
    start_scheduler: bool = True,
    job_manager: JobManager | None = None,
) -> FastAPI:
    expected_token = token or ensure_service_token()
    global_service = load_global_config().service
    jobs = job_manager or JobManager(global_service.max_background_workers)
    scheduler = RepositoryScheduler(jobs)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if start_scheduler and global_service.scheduler_enabled:
            scheduler.start()
        yield
        scheduler.stop()
        jobs.shutdown(wait=True)

    app = FastAPI(
        title="Octopus Local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    app.state.scheduler = scheduler
    if global_service.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=global_service.allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not supplied or not _constant_time_equal(supplied, expected_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

    authenticated = [Depends(authenticate)]

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "api_version": "v1",
            "bind_policy": "loopback_only",
        }

    @app.get("/v1/openapi.json", dependencies=authenticated)
    def openapi_document() -> dict[str, Any]:
        return app.openapi()

    @app.get("/v1/repositories", dependencies=authenticated)
    def repositories() -> list[dict[str, Any]]:
        global_config = load_global_config()
        payload: list[dict[str, Any]] = []
        for repository_id, repository in global_config.repositories.items():
            item: dict[str, Any] = {
                "repository_id": repository_id,
                "name": repository.name,
                "index_repository_path": repository.index_repository_path,
                "enabled": repository.enabled,
            }
            try:
                item.update(
                    _repository_payload(repository_id, Path(repository.index_repository_path))
                )
                item["available"] = True
            except (OSError, ValueError):
                item["available"] = False
            payload.append(item)
        return payload

    @app.get("/v1/repositories/{repository_id}", dependencies=authenticated)
    def repository(repository_id: str) -> dict[str, Any]:
        return _repository_payload(repository_id, _repository_path(repository_id))

    @app.post(
        "/v1/repositories/{repository_id}/updates",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def submit_update(repository_id: str, request: UpdateRequest) -> ServiceJob:
        index = _repository_path(repository_id)
        if request.leaf_only and request.foldernode_only:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "leaf_only and foldernode_only are mutually exclusive",
            )
        if jobs.active(repository_id, "update"):
            raise HTTPException(status.HTTP_409_CONFLICT, "Update is already queued or running")

        def execute() -> dict[str, Any]:
            engine = UpdateEngine(index)
            if request.dry_run:
                return engine.plan(force_path=request.force_path).model_dump(mode="json")
            return asdict(
                engine.run(
                    scan_only=request.scan_only,
                    leaf_only=request.leaf_only,
                    foldernode_only=request.foldernode_only,
                    retry_only=request.retry_only,
                    force_path=request.force_path,
                )
            )

        return jobs.submit(repository_id, "update", execute)

    @app.post(
        "/v1/repositories/{repository_id}/rebuild-search",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def rebuild_search(repository_id: str) -> ServiceJob:
        index = _repository_path(repository_id)
        return jobs.submit(
            repository_id,
            "rebuild_search",
            lambda: {"indexed_documents": SearchIndex(index).rebuild()},
        )

    @app.post("/v1/repositories/{repository_id}/search", dependencies=authenticated)
    def search(repository_id: str, request: SearchRequest) -> Any:
        search_index = SearchIndex(_repository_path(repository_id))
        if request.full:
            return search_index.full_search_report(request.query, request.limit).model_dump(
                mode="json"
            )
        return [
            result.model_dump(mode="json", exclude={"matched_terms", "match_reasons"})
            for result in search_index.search(request.query, request.limit)
        ]

    @app.post("/v1/repositories/{repository_id}/validate", dependencies=authenticated)
    def validate(repository_id: str) -> dict[str, Any]:
        return validate_repository(_repository_path(repository_id)).model_dump(mode="json")

    @app.get("/v1/repositories/{repository_id}/reports/latest", dependencies=authenticated)
    def latest_report(repository_id: str) -> dict[str, Any]:
        try:
            report = load_run_report(_repository_path(repository_id))
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        return report.model_dump(mode="json")

    @app.get("/v1/jobs", dependencies=authenticated)
    def list_jobs(repository_id: str | None = None) -> list[dict[str, Any]]:
        return [job.model_dump(mode="json") for job in jobs.list(repository_id)]

    @app.get("/v1/jobs/{job_id}", dependencies=authenticated)
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        return job.model_dump(mode="json")

    @app.get("/v1/migrations", dependencies=authenticated)
    def migrations() -> dict[str, Any]:
        global_config = load_global_config()
        indexes = [Path(item.index_repository_path) for item in global_config.repositories.values()]
        return plan_migrations(indexes).model_dump(mode="json")

    return app


def _constant_time_equal(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
