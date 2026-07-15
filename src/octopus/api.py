from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .config import (
    create_repository,
    load_global_config,
    load_repository_config,
    load_repository_state,
    save_repository_config,
)
from .credentials import (
    CredentialStoreError,
    delete_stored_ai_api_key,
    read_stored_ai_api_key,
    resolve_ai_api_key,
    save_stored_ai_api_key,
)
from .diagnostics import create_diagnostic_bundle
from .engine import UpdateEngine
from .migrations import plan_migrations
from .models import RepositoryConfig, SearchFilters, ServiceJob, TaskPack
from .onboarding import estimate_repository
from .plugin_sdk import reference_plugins_directory, run_plugin
from .providers import (
    ProviderAuthError,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
    test_ai_connection,
)
from .sample_data import default_sample_paths, materialize_sample_repository
from .search import SearchIndex
from .service_control import ensure_service_token
from .service_runtime import JobManager, RepositoryScheduler
from .task_packs import (
    TaskPackConflictError,
    TaskPackError,
    TaskPackNotFoundError,
    TaskPackVersionError,
    archive_task_pack,
    create_task_pack,
    list_task_packs,
    load_task_pack,
    render_task_pack_markdown,
    save_task_pack,
)
from .transactions import load_run_report
from .validation import validate_repository
from .workspace_api_v2 import register_workspace_routes

API_CONTRACT_VERSION = "1.0"


class UpdateRequest(BaseModel):
    dry_run: bool = False
    scan_only: bool = False
    leaf_only: bool = False
    foldernode_only: bool = False
    retry_only: bool = False
    force_path: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    mode: Literal["local", "auto"] | None = None
    full: bool | None = None
    limit: int = Field(default=20, ge=1, le=100)
    filters: SearchFilters | None = None


class RepositoryCreateRequest(BaseModel):
    raw_path: Path
    index_path: Path
    name: str | None = Field(default=None, max_length=200)
    build: bool = True


class RepositoryPreflightRequest(BaseModel):
    raw_path: Path
    index_path: Path
    ai_enabled: bool = False


class AISettingsRequest(BaseModel):
    provider: Literal["deepseek", "openai_compatible"] = "deepseek"
    base_url: str = Field(min_length=8, max_length=2_048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=8_192, repr=False)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("AI base URL must be an HTTP(S) URL without embedded credentials")
        return normalized

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("AI model cannot be empty")
        return normalized

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else ""
        return normalized or None


class AISettingsUpdateRequest(AISettingsRequest):
    enabled: bool = False
    clear_api_key: bool = False


class SampleRepositoryCreateRequest(BaseModel):
    name: str = Field(default="Octopus 示例资料", min_length=1, max_length=200)


class TaskPackCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2_000)


class TaskPackUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    task_pack: TaskPack


class TaskPackArchiveRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class TaskPackPackageRequest(BaseModel):
    output_path: Path
    confirmed_item_ids: list[str] = Field(min_length=1, max_length=1_000)


class DiagnosticCreateRequest(BaseModel):
    output_path: Path
    repository_ids: list[str] = Field(min_length=1, max_length=100)


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
        "last_successful_update_at": state.repository.last_successful_update_at,
        "states": counts,
        "queues": state.queues.model_dump(mode="json"),
    }


def _configured_ai_policy(
    config: RepositoryConfig,
    request: AISettingsRequest,
    *,
    enabled: bool,
) -> RepositoryConfig:
    updated = config.model_copy(deep=True)
    updated.ai_policy.provider = request.provider
    updated.ai_policy.base_url = request.base_url
    updated.ai_policy.model = request.model
    updated.ai_policy.complex_model = request.model
    updated.ai_policy.enabled = enabled
    return updated


def _ai_settings_payload(repository_id: str, config: RepositoryConfig) -> dict[str, Any]:
    try:
        credential = resolve_ai_api_key(repository_id, config.ai_policy.provider)
        credential_error = ""
    except CredentialStoreError:
        credential = None
        credential_error = "credential_store_unavailable"
    return {
        "repository_id": repository_id,
        "enabled": config.ai_policy.enabled,
        "provider": config.ai_policy.provider,
        "base_url": config.ai_policy.base_url,
        "model": config.ai_policy.model,
        "credential_configured": bool(credential and credential.api_key),
        "credential_source": credential.source if credential else "none",
        "credential_error": credential_error,
    }


def _ai_connection_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, ProviderAuthError):
        return "auth_failed", "API Key 验证失败，请检查密钥是否正确。"
    if isinstance(error, ProviderQuotaError):
        return "quota_exhausted", "模型账户余额或配额不足。"
    if isinstance(error, ProviderRateLimitError):
        return "rate_limited", "模型服务请求过于频繁，请稍后重试。"
    if isinstance(error, ProviderTransientError):
        return "unavailable", "暂时无法连接模型服务，请检查网络和 Base URL。"
    if isinstance(error, ProviderOutputError):
        return "invalid_response", "模型服务已连接，但返回格式不符合预期。"
    if isinstance(error, (ValueError, RuntimeError)):
        return "invalid_configuration", "模型配置不受支持，请检查服务商和模型名称。"
    return "unavailable", "模型连接测试没有完成，请稍后重试。"


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
    ui_directory = Path(__file__).resolve().parent / "ui_dist"
    if ui_directory.is_dir() and (ui_directory / "assets").is_dir():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=ui_directory / "assets"),
            name="octopus-ui-assets",
        )

    @app.middleware("http")
    async def ui_security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response
    if global_service.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=global_service.allowed_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=["Authorization", "Content-Type"],
        )

    def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bearer token required")
        supplied = authorization.removeprefix("Bearer ").strip()
        if not supplied or not _constant_time_equal(supplied, expected_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

    authenticated = [Depends(authenticate)]
    register_workspace_routes(app, authenticate, jobs)

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "api_version": "v1",
            "contract_version": API_CONTRACT_VERSION,
            "bind_policy": "loopback_only",
        }

    @app.get("/v1/contract", dependencies=authenticated)
    def contract() -> dict[str, Any]:
        return {
            "api_version": "v1",
            "contract_version": API_CONTRACT_VERSION,
            "product_version": __version__,
            "features": [
                "repository_create",
                "repository_status",
                "asynchronous_update",
                "local_and_degraded_search",
                "validation",
                "search_repair",
                "migration_plan",
                "local_diagnostics",
                "repository_preflight",
                "sample_repository",
                "task_packs",
                "task_pack_package",
                "ai_settings",
            ],
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

    @app.post("/v1/repositories/preflight", dependencies=authenticated)
    def repository_preflight(request: RepositoryPreflightRequest) -> dict[str, Any]:
        return estimate_repository(
            request.raw_path,
            request.index_path,
            ai_enabled=request.ai_enabled,
        ).model_dump(mode="json")

    @app.post(
        "/v1/repositories/sample",
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
    )
    def create_sample_repository(request: SampleRepositoryCreateRequest) -> dict[str, Any]:
        raw, index = default_sample_paths()
        raw_existed = raw.exists()
        index_existed = index.exists()
        try:
            materialize_sample_repository(raw)
            config = create_repository(
                raw,
                index,
                request.name,
                ai_enabled=False,
                require_empty=True,
            )
        except (OSError, ValueError) as error:
            if not raw_existed:
                shutil.rmtree(raw, ignore_errors=True)
            if not index_existed:
                shutil.rmtree(index, ignore_errors=True)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        repository_id = config.repository.raw_repo_id
        job = jobs.submit(
            repository_id,
            "update",
            lambda: asdict(UpdateEngine(index).run(force_path="*")),
        )
        return {
            "repository": _repository_payload(repository_id, index),
            "job": job.model_dump(mode="json"),
        }

    @app.post(
        "/v1/repositories",
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
    )
    def create_repository_endpoint(request: RepositoryCreateRequest) -> dict[str, Any]:
        try:
            config = create_repository(
                request.raw_path,
                request.index_path,
                request.name,
                ai_enabled=False,
                require_empty=True,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                str(error),
            ) from error
        repository_id = config.repository.raw_repo_id
        index = Path(config.repository.index_repository_path)
        job = (
            jobs.submit(
                repository_id,
                "update",
                lambda: asdict(UpdateEngine(index).run(force_path="*")),
            )
            if request.build
            else None
        )
        return {
            "repository": _repository_payload(repository_id, index),
            "job": job.model_dump(mode="json") if job else None,
        }

    @app.get("/v1/repositories/{repository_id}", dependencies=authenticated)
    def repository(repository_id: str) -> dict[str, Any]:
        return _repository_payload(repository_id, _repository_path(repository_id))

    @app.get(
        "/v1/repositories/{repository_id}/ai-settings",
        dependencies=authenticated,
    )
    def ai_settings(repository_id: str) -> dict[str, Any]:
        config = load_repository_config(_repository_path(repository_id))
        return _ai_settings_payload(repository_id, config)

    @app.put(
        "/v1/repositories/{repository_id}/ai-settings",
        dependencies=authenticated,
    )
    def update_ai_settings(
        repository_id: str,
        request: AISettingsUpdateRequest,
    ) -> dict[str, Any]:
        if request.clear_api_key and request.api_key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "api_key and clear_api_key cannot be supplied together",
            )
        index = _repository_path(repository_id)
        current = load_repository_config(index)
        updated = _configured_ai_policy(current, request, enabled=request.enabled)
        previous_key = ""
        try:
            previous_key = read_stored_ai_api_key(repository_id)
            if request.clear_api_key:
                delete_stored_ai_api_key(repository_id)
            elif request.api_key:
                save_stored_ai_api_key(repository_id, request.provider, request.api_key)

            credential = resolve_ai_api_key(repository_id, request.provider)
            if request.enabled and not credential.api_key:
                raise ValueError("An API key is required before AI can be enabled")
            save_repository_config(index, updated)
        except (CredentialStoreError, OSError, ValueError) as error:
            try:
                if previous_key:
                    save_stored_ai_api_key(
                        repository_id,
                        current.ai_policy.provider,
                        previous_key,
                    )
                elif request.api_key or request.clear_api_key:
                    delete_stored_ai_api_key(repository_id)
            except (CredentialStoreError, OSError, ValueError):
                pass
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Unable to save the AI settings securely",
            ) from error
        return _ai_settings_payload(repository_id, updated)

    @app.post(
        "/v1/repositories/{repository_id}/ai-settings/test",
        dependencies=authenticated,
    )
    def test_ai_settings(
        repository_id: str,
        request: AISettingsRequest,
    ) -> dict[str, Any]:
        current = load_repository_config(_repository_path(repository_id))
        candidate = _configured_ai_policy(current, request, enabled=True)
        try:
            credential = request.api_key or resolve_ai_api_key(
                repository_id,
                request.provider,
            ).api_key
            if not credential:
                return {
                    "ok": False,
                    "code": "key_not_configured",
                    "message": "请先填写 API Key。",
                }
            test_ai_connection(candidate, credential)
        except CredentialStoreError:
            return {
                "ok": False,
                "code": "credential_store_unavailable",
                "message": "无法读取 Windows 中保存的 API Key。",
            }
        except Exception as error:
            code, message = _ai_connection_error(error)
            return {"ok": False, "code": code, "message": message}
        return {
            "ok": True,
            "code": "connected",
            "message": f"已连接 {request.model}。",
        }

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
        if request.mode is not None and request.full is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "mode and legacy full cannot be supplied together",
            )
        mode: Literal["local", "auto"] = request.mode or (
            "auto" if request.full else "local"
        )
        search_index = SearchIndex(_repository_path(repository_id))
        return search_index.search_report(
            request.query,
            request.limit,
            mode,
            request.filters,
        ).model_dump(mode="json")

    @app.get(
        "/v1/repositories/{repository_id}/task-packs",
        dependencies=authenticated,
    )
    def task_pack_list(
        repository_id: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        index = _repository_path(repository_id)
        return [
            item.model_dump(mode="json")
            for item in list_task_packs(
                index, repository_id, include_archived=include_archived
            )
        ]

    @app.post(
        "/v1/repositories/{repository_id}/task-packs",
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
    )
    def task_pack_create(
        repository_id: str, request: TaskPackCreateRequest
    ) -> dict[str, Any]:
        pack = create_task_pack(
            _repository_path(repository_id), repository_id, request.title, request.goal
        )
        return pack.model_dump(mode="json")

    @app.get(
        "/v1/repositories/{repository_id}/task-packs/{task_pack_id}",
        dependencies=authenticated,
    )
    def task_pack_get(repository_id: str, task_pack_id: str) -> dict[str, Any]:
        try:
            pack = load_task_pack(_repository_path(repository_id), task_pack_id)
        except TaskPackNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except TaskPackVersionError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        if pack.repository_id != repository_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Task pack not found")
        return pack.model_dump(mode="json")

    @app.put(
        "/v1/repositories/{repository_id}/task-packs/{task_pack_id}",
        dependencies=authenticated,
    )
    def task_pack_update(
        repository_id: str,
        task_pack_id: str,
        request: TaskPackUpdateRequest,
    ) -> dict[str, Any]:
        try:
            pack = save_task_pack(
                _repository_path(repository_id),
                task_pack_id,
                repository_id,
                request.expected_revision,
                request.task_pack,
            )
        except TaskPackNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except (TaskPackConflictError, TaskPackVersionError) as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except TaskPackError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return pack.model_dump(mode="json")

    @app.post(
        "/v1/repositories/{repository_id}/task-packs/{task_pack_id}/archive",
        dependencies=authenticated,
    )
    def task_pack_archive(
        repository_id: str,
        task_pack_id: str,
        request: TaskPackArchiveRequest,
    ) -> dict[str, Any]:
        try:
            pack = archive_task_pack(
                _repository_path(repository_id),
                task_pack_id,
                repository_id,
                request.expected_revision,
            )
        except TaskPackNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except (TaskPackConflictError, TaskPackVersionError) as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        except TaskPackError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return pack.model_dump(mode="json")

    @app.get(
        "/v1/repositories/{repository_id}/task-packs/{task_pack_id}/markdown",
        response_class=PlainTextResponse,
        dependencies=authenticated,
    )
    def task_pack_markdown(repository_id: str, task_pack_id: str) -> str:
        try:
            pack = load_task_pack(_repository_path(repository_id), task_pack_id)
        except TaskPackNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        if pack.repository_id != repository_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Task pack not found")
        return render_task_pack_markdown(pack)

    @app.post(
        "/v1/repositories/{repository_id}/task-packs/{task_pack_id}/package",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def task_pack_package(
        repository_id: str,
        task_pack_id: str,
        request: TaskPackPackageRequest,
    ) -> ServiceJob:
        index = _repository_path(repository_id)
        if request.output_path.exists() and (
            not request.output_path.is_dir() or any(request.output_path.iterdir())
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Package output directory must be empty",
            )
        try:
            pack = load_task_pack(index, task_pack_id)
        except TaskPackNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        if pack.repository_id != repository_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Task pack not found")
        by_item_id = {item.item_id: item for item in pack.items}
        selected = [by_item_id.get(item_id) for item_id in request.confirmed_item_ids]
        if any(item is None for item in selected):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown task pack item")
        node_ids = {
            item.node_id
            for item in selected
            if item is not None and item.review_state == "confirmed"
        }
        if len(node_ids) != len(selected):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Only confirmed task pack items can be packaged",
            )

        def execute_package() -> dict[str, Any]:
            report = run_plugin(
                reference_plugins_directory() / "package",
                index,
                request.output_path,
                granted_permissions={
                    "index.query",
                    "export.write",
                    "export.copy_confirmed",
                },
                query=pack.goal or pack.title,
                confirmed_node_ids=node_ids,
                selected_node_ids=node_ids,
            )
            return report.model_dump(mode="json")

        return jobs.submit(repository_id, "package", execute_package)

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

    @app.post("/v1/diagnostics", dependencies=authenticated)
    def create_diagnostics(request: DiagnosticCreateRequest) -> dict[str, Any]:
        indexes = [_repository_path(repository_id) for repository_id in request.repository_ids]
        try:
            created = create_diagnostic_bundle(request.output_path, indexes)
        except (OSError, ValueError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        return {"created": True, "file": created.name, "local_only": True, "uploaded": False}

    if ui_directory.is_dir() and (ui_directory / "index.html").is_file():

        @app.get("/ui", include_in_schema=False)
        def ui_redirect() -> RedirectResponse:
            return RedirectResponse("/ui/")

        @app.get("/ui/{asset_path:path}", include_in_schema=False)
        def ui(asset_path: str) -> FileResponse:
            requested = (ui_directory / asset_path).resolve()
            if ui_directory.resolve() in requested.parents and requested.is_file():
                return FileResponse(requested)
            return FileResponse(ui_directory / "index.html")

    return app


def _constant_time_equal(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
