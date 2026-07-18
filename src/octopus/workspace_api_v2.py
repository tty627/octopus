from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from . import __version__
from .citations import DEFAULT_CITATION_STYLE, normalize_citation_style
from .config import global_config_lock, load_global_config, save_global_config
from .credentials import (
    CredentialStoreError,
    delete_stored_ai_api_key,
    read_stored_ai_api_key,
    resolve_ai_api_key,
    save_stored_ai_api_key,
)
from .export_artifacts import register_export_artifact, resolve_export_artifact
from .models import RepositoryConfig, RepositoryIdentity, ServiceJob, utc_now
from .providers import (
    PROVIDER_PRESETS,
    ProviderAuthError,
    ProviderCapabilities,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
    provider_presets,
    test_ai_connection,
)
from .research_ai import (
    ResearchTaskProposal,
    ai_index_status,
    confirm_research_proposal,
    create_research_proposal,
    run_ai_index,
    run_workspace_research,
)
from .research_export import export_research_bundle, research_bundle_filename
from .service_runtime import JobManager
from .vision import analyze_selected_page, vision_preflight
from .workspace_tasks_v2 import (
    TaskTemplateId,
    WorkspaceTask,
    WorkspaceTaskConflictError,
    WorkspaceTaskError,
    WorkspaceTaskNotFoundError,
    WorkspaceTaskVersionError,
    archive_task,
    create_task,
    list_task_templates,
    list_tasks,
    load_task,
    migrate_legacy_tasks,
    render_task_markdown,
    revalidate_task_sources,
    save_task,
)
from .workspace_v2 import (
    WorkspaceStore,
    create_workspace,
    get_workspace,
    list_workspace_payloads,
)

V2_CONTRACT_VERSION = "2.0"


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceCreateRequest(StrictRequestModel):
    raw_path: Path
    name: str | None = Field(default=None, max_length=200)


class WorkspaceSearchRequest(StrictRequestModel):
    query: str = Field(min_length=1, max_length=2_000)
    mode: Literal["local", "assisted"] = "local"
    limit: int = Field(default=30, ge=1, le=100)
    path_prefix: str = Field(default="", max_length=2_000)
    extensions: list[str] = Field(default_factory=list, max_length=100)
    readability: list[Literal["readable", "partial", "low"]] = Field(default_factory=list)
    indexing_states: list[Literal["indexed", "metadata_only", "failed", "pending"]] = Field(
        default_factory=list
    )
    source_kinds: list[str] = Field(default_factory=list, max_length=10)
    modified_from: str = ""
    modified_to: str = ""
    task_id: str = ""


class WorkspaceVisionRequest(StrictRequestModel):
    vision_enabled: bool


class WorkspaceAISettingsRequest(StrictRequestModel):
    enabled: bool = False
    provider: Literal["deepseek", "openai_compatible"] = "deepseek"
    preset: Literal["deepseek", "glm", "custom"] | None = None
    base_url: str = Field(min_length=8, max_length=2_048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=8_192, repr=False)
    clear_api_key: bool = False
    tested_capabilities: ProviderCapabilities | None = None

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
        if parsed.scheme == "http" and parsed.hostname.casefold() not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("Remote AI base URLs must use HTTPS")
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


class WorkspaceTaskCreateRequest(StrictRequestModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2_000)
    template_id: TaskTemplateId = "free_research"


class WorkspaceVisionPageRequest(StrictRequestModel):
    page_number: int = Field(default=1, ge=1, le=2_000)


class WorkspaceVisionAnalyzeRequest(WorkspaceVisionPageRequest):
    prompt: str = Field(
        default="请描述当前页面的关键信息，并指出需要人工核验的细节。",
        min_length=1,
        max_length=2_000,
    )
    confirm_image_send: bool = False


class WorkspaceTaskUpdateRequest(StrictRequestModel):
    expected_revision: int = Field(ge=1)
    task: WorkspaceTask


class WorkspaceTaskArchiveRequest(StrictRequestModel):
    expected_revision: int = Field(ge=1)


class WorkspaceTaskRevalidateRequest(StrictRequestModel):
    expected_revision: int = Field(ge=1)


class WorkspaceTaskExportRequest(StrictRequestModel):
    citation_style: str = DEFAULT_CITATION_STYLE
    include_sources: bool = False

    @field_validator("citation_style")
    @classmethod
    def validate_citation_style(cls, value: str) -> str:
        return normalize_citation_style(value)


class AIIndexRequest(StrictRequestModel):
    scope: Literal["all", "documents", "folders"] = "all"
    max_calls: int | None = Field(default=None, ge=1, le=10_000)
    retry_failed: bool = False
    # Compatibility with the first V2 client. max_calls takes precedence.
    limit: int | None = Field(default=None, ge=1, le=10_000)


class ResearchProposalRequest(StrictRequestModel):
    goal: str = Field(min_length=1, max_length=2_000)
    title: str = Field(default="", max_length=200)
    template_id: TaskTemplateId = "free_research"


class ResearchProposalConfirmRequest(StrictRequestModel):
    proposal: ResearchTaskProposal


class WorkspaceResearchRequest(StrictRequestModel):
    question: str = Field(min_length=1, max_length=2_000)
    limit: int = Field(default=50, ge=1, le=100)
    path_prefix: str = Field(default="", max_length=2_000)
    extensions: list[str] = Field(default_factory=list, max_length=100)
    readability: list[Literal["readable", "partial", "low"]] = Field(default_factory=list)
    indexing_states: list[Literal["indexed", "metadata_only", "failed", "pending"]] = Field(
        default_factory=list
    )
    source_kinds: list[str] = Field(default_factory=list, max_length=10)
    modified_from: str = ""
    modified_to: str = ""
    task_id: str = ""


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
        "preset": workspace.ai_policy.provider_preset or (
            "deepseek" if workspace.ai_policy.provider == "deepseek" else "custom"
        ),
        "base_url": workspace.ai_policy.base_url,
        "model": workspace.ai_policy.model,
        "credential_configured": bool(credential and credential.api_key),
        "credential_source": credential.source if credential else "none",
        "credential_error": credential_error,
        "vision_enabled": workspace.vision_enabled,
        "capabilities": workspace.ai_policy.tested_capabilities or {
            "text": True,
            "structured_output": workspace.ai_policy.provider in {
                "deepseek",
                "openai_compatible",
            },
            "vision": False,
            "file_upload": False,
        },
        "capabilities_tested_at": workspace.ai_policy.capabilities_tested_at,
    }


def _normalized_ai_settings(
    request: WorkspaceAISettingsRequest,
) -> tuple[str, str, str]:
    preset = request.preset or ("deepseek" if request.provider == "deepseek" else "custom")
    definition = PROVIDER_PRESETS[preset]
    provider = str(definition["provider"])
    base_url = str(definition["base_url"] or request.base_url)
    return provider, base_url, preset


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
    provider, base_url, preset = _normalized_ai_settings(request)
    config.ai_policy.provider = provider
    config.ai_policy.provider_preset = preset
    config.ai_policy.base_url = base_url
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
                "office_image_extraction",
                "archive_member_search",
                "document_evidence_search",
                "authenticated_page_preview",
                "authenticated_open_target",
                "workspace_health",
                "document_reprocess",
                "evidence_tasks",
                "research_task_templates",
                "research_bundle_export",
                "research_bundle_export_jobs",
                "signed_export_artifacts",
                "ai_document_and_folder_cards",
                "ai_research_task_proposals",
                "workspace_research_jobs",
                "task_proposal_jobs",
                "v1_task_migration",
                "explicit_vision_authorization",
                "selected_page_vision_analysis",
                "persistent_cancelable_jobs",
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

        def execute(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            result = WorkspaceStore(workspace).sync(progress)
            result["task_migration"] = migrate_legacy_tasks(workspace)
            return result

        job = jobs.submit_unique_with_progress(workspace.workspace_id, "workspace_sync", execute)
        if job is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")
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

        def execute(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            result = store.sync(progress)
            result["task_migration"] = migrate_legacy_tasks(store.workspace)
            return result

        job = jobs.submit_unique_with_progress(workspace_id, "workspace_sync", execute)
        if job is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")
        return job

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
                readability=request.readability,
                indexing_states=request.indexing_states,
                source_kinds=request.source_kinds,
                modified_from=request.modified_from,
                modified_to=request.modified_to,
                task_id=request.task_id,
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return report.model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/research",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_research(
        workspace_id: str,
        request: WorkspaceResearchRequest,
    ) -> ServiceJob:
        _workspace_store(workspace_id)
        search_options = {
            "path_prefix": request.path_prefix,
            "extensions": request.extensions,
            "readability": request.readability,
            "indexing_states": request.indexing_states,
            "source_kinds": request.source_kinds,
            "modified_from": request.modified_from,
            "modified_to": request.modified_to,
            "task_id": request.task_id,
        }
        retry_payload = {
            "kind": "workspace_research",
            "question": request.question,
            "limit": request.limit,
            "filters": search_options,
        }

        def execute(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            def relay(value: dict[str, Any]) -> None:
                progress({**value, "retry_payload": retry_payload})

            return run_workspace_research(
                workspace_id,
                request.question,
                relay,
                limit=request.limit,
                search_options=search_options,
            )

        return jobs.submit_with_progress(workspace_id, "workspace_research", execute)

    @app.get("/v2/workspaces/{workspace_id}/ai-index", dependencies=authenticated)
    def workspace_ai_index_status(workspace_id: str) -> dict[str, Any]:
        _workspace_store(workspace_id)
        return ai_index_status(workspace_id).model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/ai-index",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_ai_index(
        workspace_id: str,
        request: AIIndexRequest,
    ) -> ServiceJob:
        _workspace_store(workspace_id)
        job = jobs.submit_unique_with_progress(
            workspace_id,
            "workspace_ai_index",
            lambda progress: run_ai_index(
                workspace_id,
                request.limit,
                progress,
                scope=request.scope,
                max_calls=request.max_calls,
                retry_failed=request.retry_failed,
            ),
        )
        if job is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "AI 索引任务已经在运行。")
        return job

    @app.get("/v2/workspaces/{workspace_id}/documents", dependencies=authenticated)
    def workspace_documents(workspace_id: str) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json") for item in _workspace_store(workspace_id).list_documents()
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

    @app.get(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/members",
        dependencies=authenticated,
    )
    def workspace_document_members(
        workspace_id: str,
        document_id: str,
    ) -> list[dict[str, Any]]:
        try:
            return [
                item.model_dump(mode="json")
                for item in _workspace_store(workspace_id).list_members(document_id)
            ]
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @app.get(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/content",
        response_class=FileResponse,
        dependencies=authenticated,
    )
    def workspace_document_content(workspace_id: str, document_id: str) -> FileResponse:
        try:
            path = _workspace_store(workspace_id).content_path(document_id)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except (PermissionError, ValueError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return FileResponse(path, filename=path.name)

    @app.post(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/open-target",
        dependencies=authenticated,
    )
    def workspace_document_open_target(
        workspace_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        try:
            return _workspace_store(workspace_id).open_target(document_id)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except (PermissionError, ValueError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

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
        job = jobs.submit_unique_with_progress(
            workspace_id,
            "workspace_sync",
            lambda progress: store.reprocess_document(document_id, progress),
        )
        if job is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Workspace sync is already running")
        return job

    @app.get(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/pages/{page}/preview",
        response_class=FileResponse,
        dependencies=authenticated,
    )
    def workspace_preview(
        workspace_id: str,
        document_id: str,
        page: int,
        highlight: str = "",
        variant: Literal["base", "highlighted"] = "highlighted",
    ) -> FileResponse:
        try:
            preview = _workspace_store(workspace_id).preview_path(
                document_id,
                page,
                highlight,
                variant,
            )
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"preview_render_failed:{type(error).__name__}",
            ) from error
        actual_variant = "base" if "-base" in preview.stem else "highlighted"
        return FileResponse(
            preview,
            media_type="image/png",
            headers={"X-Octopus-Preview-Variant": actual_variant},
        )

    @app.post(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/vision/preflight",
        dependencies=authenticated,
    )
    def workspace_vision_preflight(
        workspace_id: str,
        document_id: str,
        request: WorkspaceVisionPageRequest,
    ) -> dict[str, Any]:
        try:
            return vision_preflight(
                _workspace_store(workspace_id),
                document_id,
                request.page_number,
            )
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"vision_prepare_failed:{type(error).__name__}",
            ) from error

    @app.post(
        "/v2/workspaces/{workspace_id}/documents/{document_id}/vision/analyze",
        dependencies=authenticated,
    )
    def workspace_vision_analyze(
        workspace_id: str,
        document_id: str,
        request: WorkspaceVisionAnalyzeRequest,
    ) -> dict[str, Any]:
        try:
            return analyze_selected_page(
                _workspace_store(workspace_id),
                document_id,
                request.page_number,
                request.prompt,
                confirm_image_send=request.confirm_image_send,
            )
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"vision_analysis_failed:{type(error).__name__}",
            ) from error

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
        with global_config_lock():
            config = load_global_config()
            config.workspaces[workspace_id].vision_enabled = request.vision_enabled
            save_global_config(config)
        return {"workspace_id": workspace_id, "vision_enabled": request.vision_enabled}

    @app.get("/v2/ai-provider-presets", dependencies=authenticated)
    def workspace_ai_provider_presets() -> list[dict[str, Any]]:
        return provider_presets()

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
        previous_key = ""
        provider, base_url, preset = _normalized_ai_settings(request)
        previous_provider: str = provider
        try:
            with global_config_lock():
                config = load_global_config()
                current = config.workspaces[workspace_id]
                previous_provider = current.ai_policy.provider
                previous_key = read_stored_ai_api_key(workspace_id)
                if request.clear_api_key:
                    delete_stored_ai_api_key(workspace_id)
                elif request.api_key:
                    save_stored_ai_api_key(workspace_id, provider, request.api_key)
                credential = resolve_ai_api_key(workspace_id, provider)
                if request.enabled and not credential.api_key:
                    raise ValueError("An API key is required before AI can be enabled")
                settings_changed = (
                    current.ai_policy.provider != provider
                    or current.ai_policy.base_url != base_url
                    or current.ai_policy.model != request.model
                )
                current.ai_policy.provider = provider
                current.ai_policy.provider_preset = preset
                current.ai_policy.base_url = base_url
                current.ai_policy.model = request.model
                current.ai_policy.complex_model = request.model
                current.ai_policy.enabled = request.enabled
                if request.tested_capabilities is not None:
                    current.ai_policy.tested_capabilities = request.tested_capabilities.model_dump()
                    current.ai_policy.capabilities_tested_at = utc_now()
                elif settings_changed:
                    current.ai_policy.tested_capabilities = {}
                    current.ai_policy.capabilities_tested_at = ""
                save_global_config(config)
        except (CredentialStoreError, OSError, ValueError) as error:
            try:
                if previous_key:
                    save_stored_ai_api_key(
                        workspace_id,
                        previous_provider,
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
        provider, _, _ = _normalized_ai_settings(request)
        try:
            credential = (
                request.api_key or resolve_ai_api_key(workspace_id, provider).api_key
            )
            if not credential:
                return {"ok": False, "code": "key_not_configured", "message": "请先填写 API Key。"}
            capabilities = test_ai_connection(candidate, credential)
        except CredentialStoreError:
            return {
                "ok": False,
                "code": "credential_store_unavailable",
                "message": "无法读取 Windows 中保存的 API Key。",
            }
        except Exception as error:
            code, message = _ai_error(error)
            return {"ok": False, "code": code, "message": message}
        if not isinstance(capabilities, ProviderCapabilities):
            capabilities = ProviderCapabilities(text=True)
        return {
            "ok": True,
            "code": "connected",
            "message": f"已连接 {request.model}。",
            "capabilities": capabilities.model_dump(mode="json"),
        }

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
        return create_task(
            workspace_id, request.title, request.goal, request.template_id
        ).model_dump(mode="json")

    @app.get("/v2/task-templates", dependencies=authenticated)
    def workspace_task_templates() -> list[dict[str, Any]]:
        return list_task_templates()

    @app.post(
        "/v2/workspaces/{workspace_id}/task-proposals",
        dependencies=authenticated,
    )
    def workspace_task_proposal(
        workspace_id: str,
        request: ResearchProposalRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            proposal = create_research_proposal(
                workspace_id,
                request.goal,
                request.title,
                request.template_id,
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
        return proposal.model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/task-proposals/jobs",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_task_proposal_async(
        workspace_id: str,
        request: ResearchProposalRequest,
    ) -> ServiceJob:
        _workspace_store(workspace_id)

        def execute(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            retry_payload = {
                "kind": "task_proposal",
                "goal": request.goal,
                "title": request.title,
                "template_id": request.template_id,
            }
            progress(
                {
                    "phase": "understanding",
                    "completed": 0,
                    "total": 3,
                    "retry_payload": retry_payload,
                }
            )
            progress(
                {
                    "phase": "retrieving",
                    "completed": 1,
                    "total": 3,
                    "retry_payload": retry_payload,
                }
            )
            proposal = create_research_proposal(
                workspace_id,
                request.goal,
                request.title,
                request.template_id,
            )
            progress(
                {
                    "phase": "completed",
                    "completed": 3,
                    "total": 3,
                    "evidence_count": len(proposal.candidates),
                    "retry_payload": retry_payload,
                }
            )
            return {"proposal": proposal.model_dump(mode="json")}

        return jobs.submit_with_progress(workspace_id, "task_proposal", execute)

    @app.post(
        "/v2/workspaces/{workspace_id}/task-proposals/confirm",
        dependencies=authenticated,
    )
    def workspace_task_proposal_confirm(
        workspace_id: str,
        request: ResearchProposalConfirmRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            task = confirm_research_proposal(workspace_id, request.proposal)
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return task.model_dump(mode="json")

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

    @app.post(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}/revalidate",
        dependencies=authenticated,
    )
    def workspace_task_revalidate(
        workspace_id: str,
        task_id: str,
        request: WorkspaceTaskRevalidateRequest,
    ) -> dict[str, Any]:
        _workspace_store(workspace_id)
        try:
            task = revalidate_task_sources(
                workspace_id,
                task_id,
                request.expected_revision,
            )
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error
        return task.model_dump(mode="json")

    @app.post(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}/export",
        response_class=FileResponse,
        dependencies=authenticated,
    )
    def workspace_task_export(
        workspace_id: str,
        task_id: str,
        request: WorkspaceTaskExportRequest,
    ) -> FileResponse:
        _workspace_store(workspace_id)
        try:
            task = load_task(workspace_id, task_id)
            download_name = research_bundle_filename(task)
            output = export_research_bundle(
                task,
                citation_style=normalize_citation_style(request.citation_style),
                include_sources=request.include_sources,
            )
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error
        except (OSError, ValueError, FileNotFoundError) as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        return FileResponse(
            output,
            media_type="application/zip",
            filename=download_name,
            background=BackgroundTask(output.unlink, missing_ok=True),
        )

    @app.post(
        "/v2/workspaces/{workspace_id}/tasks/{task_id}/exports",
        response_model=ServiceJob,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=authenticated,
    )
    def workspace_task_export_async(
        workspace_id: str,
        task_id: str,
        request: WorkspaceTaskExportRequest,
    ) -> ServiceJob:
        _workspace_store(workspace_id)
        try:
            task = load_task(workspace_id, task_id)
        except WorkspaceTaskError as error:
            raise _handle_task_error(error) from error

        def execute(progress: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
            retry_payload = {
                "kind": "task_export",
                "task_id": task_id,
                "citation_style": request.citation_style,
                "include_sources": request.include_sources,
            }

            def relay(value: dict[str, Any]) -> None:
                progress({**value, "retry_payload": retry_payload})

            output = export_research_bundle(
                task,
                citation_style=normalize_citation_style(request.citation_style),
                include_sources=request.include_sources,
                progress_callback=relay,
            )
            try:
                artifact = register_export_artifact(
                    workspace_id,
                    output,
                    file_name=research_bundle_filename(task),
                )
            finally:
                output.unlink(missing_ok=True)
            result = artifact.model_dump(mode="json")
            result["progress"] = {
                "phase": "completed",
                "completed": len(task.items),
                "total": len(task.items),
                "retry_payload": retry_payload,
            }
            return result

        return jobs.submit_with_progress(workspace_id, "task_export", execute)

    @app.get(
        "/v2/workspaces/{workspace_id}/exports/{artifact_id}",
        response_class=FileResponse,
        dependencies=authenticated,
    )
    def workspace_export_artifact(
        workspace_id: str,
        artifact_id: str,
    ) -> FileResponse:
        _workspace_store(workspace_id)
        try:
            artifact, path = resolve_export_artifact(workspace_id, artifact_id)
        except FileNotFoundError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
        return FileResponse(
            path,
            media_type="application/zip",
            filename=artifact.file_name,
            headers={
                "X-Octopus-Artifact-Id": artifact.artifact_id,
                "X-Octopus-Artifact-Sha256": artifact.sha256,
                "X-Octopus-Artifact-Expires-At": artifact.expires_at,
            },
        )

    @app.get("/v2/workspaces/{workspace_id}/changes", dependencies=authenticated)
    def workspace_changes(
        workspace_id: str,
        limit: int = Query(default=100, ge=1, le=1_000),
        since: str = Query(default="", max_length=64),
        include_acknowledged: bool = Query(default=False),
    ) -> list[dict[str, Any]]:
        store = _workspace_store(workspace_id)
        return store.list_changes(
            limit=limit,
            since=since,
            include_acknowledged=include_acknowledged,
        )

    @app.get("/v2/jobs", dependencies=authenticated)
    def workspace_jobs(workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in jobs.list(workspace_id)]

    @app.get("/v2/jobs/{job_id}", dependencies=authenticated)
    def workspace_job(job_id: str, workspace_id: str) -> dict[str, Any]:
        _workspace_store(workspace_id)
        job = jobs.get(job_id)
        if job is None or job.repository_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        return job.model_dump(mode="json")

    @app.post("/v2/jobs/{job_id}/cancel", dependencies=authenticated)
    def workspace_job_cancel(job_id: str, workspace_id: str) -> dict[str, Any]:
        _workspace_store(workspace_id)
        existing = jobs.get(job_id)
        if existing is None or existing.repository_id != workspace_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        job = jobs.cancel(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        return job.model_dump(mode="json")
