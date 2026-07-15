from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator

from . import __version__
from .config import load_global_config, save_global_config
from .credentials import (
    CredentialStoreError,
    delete_stored_ai_api_key,
    read_stored_ai_api_key,
    resolve_ai_api_key,
    save_stored_ai_api_key,
)
from .models import RepositoryConfig, RepositoryIdentity, ServiceJob
from .providers import (
    ProviderAuthError,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
    test_ai_connection,
)
from .service_runtime import JobManager
from .workspace_tasks_v2 import (
    WorkspaceTask,
    WorkspaceTaskConflictError,
    WorkspaceTaskError,
    WorkspaceTaskNotFoundError,
    WorkspaceTaskVersionError,
    archive_task,
    create_task,
    list_tasks,
    load_task,
    migrate_legacy_tasks,
    render_task_markdown,
    save_task,
)
from .workspace_v2 import (
    WorkspaceStore,
    create_workspace,
    get_workspace,
    list_workspace_payloads,
)

V2_CONTRACT_VERSION = "2.0"


class WorkspaceCreateRequest(BaseModel):
    raw_path: Path
    name: str | None = Field(default=None, max_length=200)


class WorkspaceSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    mode: Literal["local", "assisted"] = "local"
    limit: int = Field(default=30, ge=1, le=100)
    path_prefix: str = Field(default="", max_length=2_000)
    extensions: list[str] = Field(default_factory=list, max_length=100)


class WorkspaceVisionRequest(BaseModel):
    vision_enabled: bool


class WorkspaceAISettingsRequest(BaseModel):
    enabled: bool = False
    provider: Literal["deepseek", "openai_compatible"] = "deepseek"
    base_url: str = Field(min_length=8, max_length=2_048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=8_192, repr=False)
    clear_api_key: bool = False

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
            raise ValueError("AI base URL must be an HTTP(S) URL without credentials")
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


class WorkspaceTaskCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2_000)


class WorkspaceTaskUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    task: WorkspaceTask


class WorkspaceTaskArchiveRequest(BaseModel):
    expected_revision: int = Field(ge=1)


def _workspace_store(workspace_id: str) -> WorkspaceStore:
    try:
        return WorkspaceStore(get_workspace(workspace_id))
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found") from error


def _workspace_ai_payload(workspace_id: str) -> dict[str, Any]:
    workspace = _workspace_store(workspace_id).workspace
    try:
        credential = resolve_ai_api_key(workspace_id, workspace.ai_policy.provider)
        credential_error = ""
    except CredentialStoreError:
        credential = None
        credential_error = "credential_store_unavailable"
    return {
        "workspace_id": workspace_id,
        "enabled": workspace.ai_policy.enabled,
        "provider": workspace.ai_policy.provider,
        "base_url": workspace.ai_policy.base_url,
        "model": workspace.ai_policy.model,
        "credential_configured": bool(credential and credential.api_key),
        "credential_source": credential.source if credential else "none",
        "credential_error": credential_error,
        "vision_enabled": workspace.vision_enabled,
    }


def _ai_candidate(workspace_id: str, request: WorkspaceAISettingsRequest) -> RepositoryConfig:
    workspace = _workspace_store(workspace_id).workspace
    config = RepositoryConfig(
        repository=RepositoryIdentity(
            raw_repo_id=workspace_id,
            raw_repository_path=workspace.raw_path,
            index_repository_path=workspace.storage_path,
            repository_name=workspace.name,
        )
    )
    config.ai_policy = workspace.ai_policy.model_copy(deep=True)
    config.ai_policy.provider = request.provider
    config.ai_policy.base_url = request.base_url
    config.ai_policy.model = request.model
    config.ai_policy.complex_model = request.model
    config.ai_policy.enabled = request.enabled
    return config


def _ai_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, ProviderAuthError):
        return "auth_failed", "API Key 验证失败，请检查密钥。"
    if isinstance(error, ProviderQuotaError):
        return "quota_exhausted", "模型账户余额或配额不足。"
    if isinstance(error, ProviderRateLimitError):
        return "rate_limited", "模型服务请求过于频繁，请稍后重试。"
    if isinstance(error, ProviderTransientError):
        return "unavailable", "暂时无法连接模型服务。"
    if isinstance(error, ProviderOutputError):
        return "invalid_response", "模型服务返回了无效响应。"
    return "invalid_configuration", "模型配置无法使用。"


def _handle_task_error(error: WorkspaceTaskError) -> HTTPException:
    if isinstance(error, WorkspaceTaskNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, (WorkspaceTaskConflictError, WorkspaceTaskVersionError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


def register_workspace_routes(
    app: FastAPI,
    authenticate: Callable[..., None],
    jobs: JobManager,
) -> None:
    authenticated = [Depends(authenticate)]

    @app.get("/v2/health")
    def v2_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "api_version": "v2",
            "contract_version": V2_CONTRACT_VERSION,
            "bind_policy": "loopback_only",
        }

    @app.get("/v2/contract", dependencies=authenticated)
    def v2_contract() -> dict[str, Any]:
        return {
            "api_version": "v2",
            "contract_version": V2_CONTRACT_VERSION,
            "product_version": __version__,
            "features": [
                "workspace_create",
                "hidden_sqlite_index",
                "pdfium_ocr_pipeline",
                "document_evidence_search",
                "authenticated_page_preview",
                "workspace_health",
                "document_reprocess",
                "evidence_tasks",
                "v1_task_migration",
                "explicit_vision_authorization",
            ],
        }

    @app.get("/v2/openapi.json", dependencies=authenticated)
    def v2_openapi() -> dict[str, Any]:
        return app.openapi()

    @app.get("/v2/workspaces", dependencies=authenticated)
    def workspaces() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in list_workspace_payloads()]

    @app.post(
        "/v2/workspaces",
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
    )
    def workspace_create(request: WorkspaceCreateRequest) -> dict[str, Any]:
        try:
            workspace = create_workspace(request.raw_path, request.name)
        except (OSError, ValueError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        if jobs.active(workspace.workspace_id, "workspace_sync"):
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")

        def execute() -> dict[str, Any]:
            result = WorkspaceStore(workspace).sync()
            result["task_migration"] = migrate_legacy_tasks(workspace)
            return result

        job = jobs.submit(workspace.workspace_id, "workspace_sync", execute)
        return {
            "workspace": WorkspaceStore(workspace).payload().model_dump(mode="json"),
            "job": job.model_dump(mode="json"),
        }

    @app.get("/v2/workspaces/{workspace_id}", dependencies=authenticated)
    def workspace_get(workspace_id: str) -> dict[str, Any]:
        return _workspace_store(workspace_id).payload().model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/sync",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_sync(workspace_id: str) -> ServiceJob:
        store = _workspace_store(workspace_id)
        if jobs.active(workspace_id, "workspace_sync"):
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")

        def execute() -> dict[str, Any]:
            result = store.sync()
            result["task_migration"] = migrate_legacy_tasks(store.workspace)
            return result

        return jobs.submit(workspace_id, "workspace_sync", execute)

    @app.post("/v2/workspaces/{workspace_id}/search", dependencies=authenticated)
    def workspace_search(
        workspace_id: str,
        request: WorkspaceSearchRequest,
    ) -> dict[str, Any]:
        try:
            report = _workspace_store(workspace_id).search(
                request.query,
                limit=request.limit,
                mode=request.mode,
                path_prefix=request.path_prefix,
                extensions=request.extensions,
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return report.model_dump(mode="json")

    @app.get("/v2/workspaces/{workspace_id}/documents", dependencies=authenticated)
    def workspace_documents(workspace_id: str) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in _workspace_store(workspace_id).list_documents()
        ]

    @app.get(
        "/v2/workspaces/{workspace_id}/documents/{document_id}",
        dependencies=authenticated,
    )
    def workspace_document(workspace_id: str, document_id: str) -> dict[str, Any]:
        try:
            document = _workspace_store(workspace_id).get_document(document_id)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        return document.model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/reprocess",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_reprocess(workspace_id: str, document_id: str) -> ServiceJob:
        store = _workspace_store(workspace_id)
        try:
            store.get_document(document_id)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        if jobs.active(workspace_id, "workspace_sync"):
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")
        return jobs.submit(
            workspace_id,
            "workspace_sync",
            lambda: store.reprocess_document(document_id),
        )

    @app.get(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/pages/{page}/preview",
        response_class=FileResponse,
        dependencies=authenticated,
    )
    def workspace_preview(workspace_id: str, document_id: str, page: int) -> FileResponse:
        try:
            preview = _workspace_store(workspace_id).preview_path(document_id, page)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return FileResponse(preview, media_type="image/png")

    @app.get("/v2/workspaces/{workspace_id}/vision-authorization", dependencies=authenticated)
    def vision_authorization(workspace_id: str) -> dict[str, Any]:
        workspace = _workspace_store(workspace_id).workspace
        return {"workspace_id": workspace_id, "vision_enabled": workspace.vision_enabled}

    @app.put("/v2/workspaces/{workspace_id}/vision-authorization", dependencies=authenticated)
    def vision_authorization_update(
        workspace_id: str,
        request: WorkspaceVisionRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        config = load_global_config()
        config.workspaces[workspace_id].vision_enabled = request.vision_enabled
        save_global_config(config)
        return {"workspace_id": workspace_id, "vision_enabled": request.vision_enabled}

    @app.get("/v2/workspaces/{workspace_id}/ai-settings", dependencies=authenticated)
    def workspace_ai_settings(workspace_id: str) -> dict[str, Any]:
        return _workspace_ai_payload(workspace_id)

    @app.put("/v2/workspaces/{workspace_id}/ai-settings", dependencies=authenticated)
    def workspace_ai_settings_update(
        workspace_id: str,
        request: WorkspaceAISettingsRequest,
    ) -> dict[str, Any]:
        if request.clear_api_key and request.api_key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "api_key and clear_api_key cannot be supplied together",
            )
        _workspace_store(workspace_id)
        config = load_global_config()
        current = config.workspaces[workspace_id]
        previous_key = ""
        try:
            previous_key = read_stored_ai_api_key(workspace_id)
            if request.clear_api_key:
                delete_stored_ai_api_key(workspace_id)
            elif request.api_key:
                save_stored_ai_api_key(workspace_id, request.provider, request.api_key)
            credential = resolve_ai_api_key(workspace_id, request.provider)
            if request.enabled and not credential.api_key:
                raise ValueError("An API key is required before AI can be enabled")
            current.ai_policy.provider = request.provider
            current.ai_policy.base_url = request.base_url
            current.ai_policy.model = request.model
            current.ai_policy.complex_model = request.model
            current.ai_policy.enabled = request.enabled
            save_global_config(config)
        except (CredentialStoreError, OSError, ValueError) as error:
            try:
                if previous_key:
                    save_stored_ai_api_key(
                        workspace_id,
                        current.ai_policy.provider,
                        previous_key,
                    )
                elif request.api_key or request.clear_api_key:
                    delete_stored_ai_api_key(workspace_id)
            except (CredentialStoreError, OSError, ValueError):
                pass
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Unable to save AI settings securely",
            ) from error
        return _workspace_ai_payload(workspace_id)

    @app.post("/v2/workspaces/{workspace_id}/ai-settings/test", dependencies=authenticated)
    def workspace_ai_settings_test(
        workspace_id: str,
        request: WorkspaceAISettingsRequest,
    ) -> dict[str, Any]:
        candidate = _ai_candidate(workspace_id, request)
        try:
            credential = request.api_key or resolve_ai_api_key(
                workspace_id, request.provider
            ).api_key
            if not credential:
                return {"ok": False, "code": "key_not_configured", "message": "请先填写 API Key。"}
            test_ai_connection(candidate, credential)
        except CredentialStoreError:
            return {
                "ok": False,
                "code": "credential_store_unavailable",
                "message": "无法读取 Windows 中保存的 API Key。",
            }
        except Exception as error:
            code, message = _ai_error(error)
            return {"ok": False, "code": code, "message": message}
        return {"ok": True, "code": "connected", "message": f"已连接 {request.model}。"}

    @app.get("/v2/workspaces/{workspace_id}/tasks", dependencies=authenticated)
    def workspace_task_list(
        workspace_id: str,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        workspace = _workspace_store(workspace_id).workspace
        migrate_legacy_tasks(workspace)
        return [
            item.model_dump(mode="json")
            for item in list_tasks(workspace_id, include_archived=include_archived)
        ]

    @app.post(
        "/v2/workspaces/{workspace_id}/tasks",
        status_code=status.HTTP_201_CREATED,
        dependencies=authenticated,
    )
    def workspace_task_create(
        workspace_id: str,
        request: WorkspaceTaskCreateRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        return create_task(workspace_id, request.title, request.goal).model_dump(mode="json")

    @app.get(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}",
        dependencies=authenticated,
    )
    def workspace_task_get(workspace_id: str, task_id: str) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            task = load_task(workspace_id, task_id)
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error
        return task.model_dump(mode="json")

    @app.put(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}",
        dependencies=authenticated,
    )
    def workspace_task_update(
        workspace_id: str,
        task_id: str,
        request: WorkspaceTaskUpdateRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            task = save_task(
                workspace_id,
                task_id,
                request.expected_revision,
                request.task,
            )
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error
        return task.model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}/archive",
        dependencies=authenticated,
    )
    def workspace_task_archive(
        workspace_id: str,
        task_id: str,
        request: WorkspaceTaskArchiveRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            task = archive_task(workspace_id, task_id, request.expected_revision)
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error
        return task.model_dump(mode="json")

    @app.get(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}/markdown",
        response_class=PlainTextResponse,
        dependencies=authenticated,
    )
    def workspace_task_markdown(workspace_id: str, task_id: str) -> str:
        _workspace_store(workspace_id)
        try:
            return render_task_markdown(load_task(workspace_id, task_id))
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error

    @app.get("/v2/jobs", dependencies=authenticated)
    def workspace_jobs(workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in jobs.list(workspace_id)]

    @app.get("/v2/jobs/{job_id}", dependencies=authenticated)
    def workspace_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        return job.model_dump(mode="json")
