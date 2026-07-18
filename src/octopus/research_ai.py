from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from .models import (
    AIUsage,
    ExtractedDocument,
    ExtractionEvidence,
    GeneratedSummary,
    GlobalWorkspace,
    OctopusModel,
    RepositoryConfig,
    RepositoryIdentity,
    utc_now,
)
from .prompts import PROMPT_VERSION, RESEARCH_ANSWER_PROMPT, RESEARCH_TASK_PROMPT
from .providers import (
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    create_provider,
)
from .utils import atomic_write_json
from .workspace_sources import EvidenceLocator, SourceRef
from .workspace_tasks_v2 import (
    TaskTemplateId,
    WorkspaceTask,
    WorkspaceTaskError,
    WorkspaceTaskItem,
    WorkspaceTaskSlot,
    _validate_task,
)
from .workspace_v2 import WorkspaceSearchResult, WorkspaceStore, get_workspace


class AIDocumentCard(OctopusModel):
    document_id: str
    content_hash: str
    status: Literal["ready", "pending", "failed"] = "ready"
    one_sentence_summary: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    recommended_reading: list[str] = Field(default_factory=list)
    prompt_version: str = PROMPT_VERSION
    model: str = ""
    updated_at: str = ""
    error: str = ""


AIStatus = Literal["ready", "pending", "failed"]


def _ai_status(value: object) -> AIStatus:
    normalized = str(value or "").casefold()
    if normalized == "ready":
        return "ready"
    if normalized == "pending":
        return "pending"
    return "failed"


class AIFolderCard(OctopusModel):
    folder_path: str
    fingerprint: str
    status: Literal["ready", "pending", "failed"] = "ready"
    one_sentence_summary: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    topic_keywords: list[str] = Field(default_factory=list)
    recommended_reading: list[str] = Field(default_factory=list)
    prompt_version: str = PROMPT_VERSION
    model: str = ""
    updated_at: str = ""
    error: str = ""


class AIIndexStatus(OctopusModel):
    workspace_id: str
    document_count: int = 0
    indexed_document_count: int = 0
    pending_document_count: int = 0
    failed_document_count: int = 0
    folder_count: int = 0
    indexed_folder_count: int = 0
    pending_folder_count: int = 0
    failed_folder_count: int = 0
    estimated_calls: int = 0
    last_run_at: str = ""
    last_error: str = ""


class ResearchCandidate(OctopusModel):
    candidate_id: str
    document_id: str
    content_hash: str
    name: str
    relative_path: str
    page_number: int | None = None
    locator: EvidenceLocator | None = None
    excerpt: str = ""
    reason: str = ""
    quality_score: float = 0.0
    source_ref: SourceRef | None = None
    overview: str = ""


class ResearchSlotProposal(OctopusModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    required: bool = False
    candidate_ids: list[str] = Field(default_factory=list)
    rationales: dict[str, str] = Field(default_factory=dict)


class ResearchTaskProposal(OctopusModel):
    workspace_id: str = ""
    template_id: TaskTemplateId = "free_research"
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2_000)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    slots: list[ResearchSlotProposal] = Field(default_factory=list)
    candidates: list[ResearchCandidate] = Field(default_factory=list)


class ResearchCitation(OctopusModel):
    citation_id: str
    document_id: str
    name: str
    relative_path: str
    page_number: int | None = None
    locator: EvidenceLocator | None = None
    excerpt: str = ""


def _research_queries(question: str) -> list[str]:
    normalized = " ".join(question.split()).strip()
    fragments = [
        " ".join(value.split()).strip(" ，。；;：:！？!?")
        for value in re.split(r"[，。；;：:！？!?]|以及|并且|同时", normalized)
    ]
    values: list[str] = []
    for value in [normalized, *fragments]:
        if len(value) < 2 or value in values:
            continue
        values.append(value)
    return values[:4]


def run_workspace_research(
    workspace_id: str,
    question: str,
    progress: Callable[[dict[str, Any]], None] | None = None,
    *,
    limit: int = 50,
    search_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    workspace = get_workspace(workspace_id)
    store = WorkspaceStore(workspace)
    queries = _research_queries(question)
    if progress:
        progress({"phase": "understanding", "completed": 0, "total": len(queries)})

    by_document: dict[str, WorkspaceSearchResult] = {}
    options = dict(search_options or {})
    for index, query in enumerate(queries, start=1):
        if progress:
            progress(
                {
                    "phase": "retrieving",
                    "completed": index - 1,
                    "total": len(queries),
                    "current_query": query,
                }
            )
        report = store.search(query, limit=limit, mode="local", **options)
        for result in report.results:
            existing = by_document.get(result.document_id)
            if existing is None:
                by_document[result.document_id] = result
                continue
            evidence = [
                existing.best_evidence,
                *existing.additional_evidence,
                result.best_evidence,
                *result.additional_evidence,
            ]
            unique = []
            seen: set[tuple[int | None, str]] = set()
            for item in evidence:
                key = (item.page_number, item.excerpt)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(item)
            by_document[result.document_id] = existing.model_copy(
                update={"best_evidence": unique[0], "additional_evidence": unique[1:3]}
            )
    results = [
        result.model_copy(update={"rank": rank})
        for rank, result in enumerate(by_document.values(), start=1)
    ][:limit]
    citations = [
        ResearchCitation(
            citation_id=f"R{index}",
            document_id=result.document_id,
            name=result.name,
            relative_path=result.relative_path,
            page_number=result.best_evidence.page_number,
            locator=result.best_evidence.locator or result.locator,
            excerpt=result.best_evidence.excerpt,
        )
        for index, result in enumerate(results, start=1)
    ]
    if progress:
        progress(
            {
                "phase": "composing",
                "completed": len(queries),
                "total": len(queries),
                "evidence_count": len(citations),
            }
        )

    actual_mode = "research"
    degradation_reason = ""
    warnings: list[str] = []
    answer = (
        f"在当前资料空间中找到 {len(results)} 份相关资料。"
        if results
        else "当前资料空间没有找到足以回答该问题的证据。"
    )
    usage: dict[str, Any] = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "estimated_cost": None,
    }
    if results:
        try:
            provider = create_provider(_config_for(workspace), require_network=True)
            output = provider._json_call(  # type: ignore[attr-defined]
                "workspace_research",
                RESEARCH_ANSWER_PROMPT,
                {
                    "question": question,
                    "subqueries": queries,
                    "candidates": [item.model_dump(mode="json") for item in citations],
                },
            )
            allowed = {item.citation_id for item in citations}
            raw_ids = output.get("citation_ids", [])
            citation_ids = (
                [str(value) for value in raw_ids if str(value) in allowed]
                if isinstance(raw_ids, list)
                else []
            )
            candidate_answer = str(output.get("answer", "")).strip()
            invalid_refs = {
                match for match in re.findall(r"\[([A-Za-z]+\d+)\]", candidate_answer)
                if match not in allowed
            }
            if invalid_refs:
                raise ValueError("AI answer cited evidence outside the supplied candidates")
            if candidate_answer:
                answer = candidate_answer
            raw_warnings = output.get("warnings", [])
            if isinstance(raw_warnings, list):
                warnings.extend(str(value) for value in raw_warnings if str(value))
            if citation_ids and not any(f"[{value}]" in answer for value in citation_ids):
                answer = f"{answer}\n\n引用：" + " ".join(f"[{value}]" for value in citation_ids)
            provider_usage = getattr(provider, "usage", None)
            if provider_usage is not None:
                usage = provider_usage.model_dump(mode="json")
        except Exception as error:
            actual_mode = "degraded"
            degradation_reason = type(error).__name__
            warnings.append("辅助模型不可用，本次保留本地检索结果。")
    duration_ms = int((time.perf_counter() - started) * 1000)
    usage["duration_ms"] = max(int(usage.get("duration_ms", 0)), duration_ms)
    result_payload = {
        "query": question.strip(),
        "requested_mode": "research",
        "actual_mode": actual_mode,
        "degradation_reason": degradation_reason,
        "answer": answer,
        "results": [item.model_dump(mode="json") for item in results],
        "candidate_count": len(results),
        "duration_ms": duration_ms,
        "subqueries": queries,
        "citations": [item.model_dump(mode="json") for item in citations],
        "warnings": warnings,
        "usage": usage,
        "cost_known": usage.get("estimated_cost") is not None,
    }
    if progress:
        progress(
            {
                "phase": "completed",
                "completed": len(queries),
                "total": len(queries),
                "evidence_count": len(citations),
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "duration_ms": duration_ms,
                "estimated_cost": usage.get("estimated_cost"),
                "cost_known": usage.get("estimated_cost") is not None,
            }
        )
    return result_payload


def _config_for(workspace: GlobalWorkspace) -> RepositoryConfig:
    config = RepositoryConfig(
        repository=RepositoryIdentity(
            raw_repo_id=workspace.workspace_id,
            raw_repository_path=workspace.raw_path,
            index_repository_path=workspace.storage_path,
            repository_name=workspace.name,
        )
    )
    config.ai_policy = workspace.ai_policy.model_copy(deep=True)
    return config


def _folder_paths(store: WorkspaceStore, connection: Any) -> list[str]:
    values: set[str] = set()
    for row in connection.execute("SELECT relative_path FROM documents").fetchall():
        path = Path(str(row["relative_path"])).parent.as_posix()
        while path not in {"", "."}:
            values.add(path)
            path = Path(path).parent.as_posix()
        values.add(".")
    return sorted(values, key=lambda value: (value.count("/"), value))


def _folder_fingerprint(connection: Any, folder: str) -> str:
    prefix = "" if folder == "." else folder.rstrip("/") + "/"
    rows = connection.execute(
        "SELECT document_id, content_hash, relative_path FROM documents "
        "WHERE relative_path LIKE ? ORDER BY relative_path",
        (f"{prefix}%",),
    ).fetchall()
    payload = "\n".join(
        f"{row['document_id']}|{row['content_hash']}|{row['relative_path']}" for row in rows
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _card_from_row(row: Any) -> AIDocumentCard:
    try:
        payload = json.loads(str(row["card_json"] or "{}"))
    except ValueError:
        payload = {}
    return AIDocumentCard(
        document_id=str(row["document_id"]),
        content_hash=str(row["content_hash"]),
        status=_ai_status(row["status"]),
        prompt_version=str(row["prompt_version"]),
        model=str(row["model"]),
        updated_at=str(row["updated_at"]),
        error=str(row["error"]),
        **payload,
    )


def _folder_card_from_row(row: Any) -> AIFolderCard:
    try:
        payload = json.loads(str(row["card_json"] or "{}"))
    except ValueError:
        payload = {}
    return AIFolderCard(
        folder_path=str(row["folder_path"]),
        fingerprint=str(row["fingerprint"]),
        status=_ai_status(row["status"]),
        prompt_version=str(row["prompt_version"]),
        model=str(row["model"]),
        updated_at=str(row["updated_at"]),
        error=str(row["error"]),
        **payload,
    )


def _ensure_ai_schema(store: WorkspaceStore, connection: Any) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_document_cards (
            document_id TEXT PRIMARY KEY REFERENCES documents(document_id) ON DELETE CASCADE,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            card_json TEXT NOT NULL DEFAULT '{}',
            prompt_version TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ai_folder_cards (
            folder_path TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            card_json TEXT NOT NULL DEFAULT '{}',
            prompt_version TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT ''
        );
        """
    )


def ai_index_status(workspace_id: str) -> AIIndexStatus:
    store = WorkspaceStore(get_workspace(workspace_id))
    with closing(store._connect()) as connection:
        _ensure_ai_schema(store, connection)
        documents = connection.execute(
            "SELECT document_id, content_hash, indexing_state FROM documents"
        ).fetchall()
        cards = {
            str(row["document_id"]): row
            for row in connection.execute("SELECT * FROM ai_document_cards").fetchall()
        }
        indexed = failed = 0
        pending_ids: list[str] = []
        for row in documents:
            card = cards.get(str(row["document_id"]))
            if card is not None and str(card["content_hash"]) == str(row["content_hash"]):
                if str(card["status"]) == "failed":
                    failed += 1
                else:
                    indexed += 1
            elif str(row["indexing_state"]) != "failed":
                pending_ids.append(str(row["document_id"]))
        folders = _folder_paths(store, connection)
        folder_rows = {
            str(row["folder_path"]): row
            for row in connection.execute("SELECT * FROM ai_folder_cards").fetchall()
        }
        indexed_folders = failed_folders = 0
        pending_folders = 0
        for folder in folders:
            row = folder_rows.get(folder)
            fingerprint = _folder_fingerprint(connection, folder)
            if row is None or str(row["fingerprint"]) != fingerprint:
                pending_folders += 1
            elif str(row["status"]) == "failed":
                failed_folders += 1
            else:
                indexed_folders += 1
        last = connection.execute(
            "SELECT value FROM workspace_metadata WHERE key = 'ai_index_last_run'"
        ).fetchone()
        last_error = connection.execute(
            "SELECT value FROM workspace_metadata WHERE key = 'ai_index_last_error'"
        ).fetchone()
        return AIIndexStatus(
            workspace_id=workspace_id,
            document_count=len(documents),
            indexed_document_count=indexed,
            pending_document_count=len(pending_ids),
            failed_document_count=failed,
            folder_count=len(folders),
            indexed_folder_count=indexed_folders,
            pending_folder_count=pending_folders,
            failed_folder_count=failed_folders,
            estimated_calls=len(pending_ids) + pending_folders,
            last_run_at=str(last[0]) if last else "",
            last_error=str(last_error[0]) if last_error else "",
        )


def _document_input(connection: Any, document_id: str) -> tuple[Any, ExtractedDocument] | None:
    row = connection.execute(
        "SELECT * FROM documents WHERE document_id = ?", (document_id,)
    ).fetchone()
    if row is None:
        return None
    passages = connection.execute(
        "SELECT page_number, heading, text, locator_json FROM passages "
        "WHERE document_id = ? ORDER BY page_number, ordinal LIMIT 18",
        (document_id,),
    ).fetchall()
    evidence = [
        ExtractionEvidence(
            locator=str(item["locator_json"] or f"page:{item['page_number'] or ''}"),
            kind="passage",
            text_excerpt=str(item["text"])[:500],
        )
        for item in passages
    ]
    text = "\n\n".join(str(item["text"])[:2_500] for item in passages)
    document = ExtractedDocument(
        name=str(row["name"]),
        document_type=str(row["extension"]).lstrip(".") or "document",
        text=text,
        structure=[str(item["heading"]) for item in passages if str(item["heading"])],
        metadata={
            "relative_path": str(row["relative_path"]),
            "title": str(row["title"]),
            "readability": str(row["readability"]),
            "quality_flags": json.loads(str(row["quality_flags_json"] or "[]")),
        },
        evidence=evidence,
    )
    return row, document


def _store_document_card(connection: Any, card: AIDocumentCard) -> None:
    payload = card.model_dump(
        mode="json",
        exclude={
            "document_id",
            "content_hash",
            "status",
            "prompt_version",
            "model",
            "updated_at",
            "error",
        },
    )
    connection.execute(
        "INSERT INTO ai_document_cards("
        "document_id, content_hash, status, card_json, prompt_version, model, updated_at, error"
        ") "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(document_id) DO UPDATE SET "
        "content_hash=excluded.content_hash,status=excluded.status,card_json=excluded.card_json,"
        "prompt_version=excluded.prompt_version,model=excluded.model,updated_at=excluded.updated_at,error=excluded.error",
        (
            card.document_id,
            card.content_hash,
            card.status,
            json.dumps(payload, ensure_ascii=False),
            card.prompt_version,
            card.model,
            card.updated_at,
            card.error,
        ),
    )


def _store_folder_card(connection: Any, card: AIFolderCard) -> None:
    payload = card.model_dump(
        mode="json",
        exclude={
            "folder_path",
            "fingerprint",
            "status",
            "prompt_version",
            "model",
            "updated_at",
            "error",
        },
    )
    connection.execute(
        "INSERT INTO ai_folder_cards("
        "folder_path, fingerprint, status, card_json, prompt_version, model, updated_at, error"
        ") "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(folder_path) DO UPDATE SET "
        "fingerprint=excluded.fingerprint,status=excluded.status,card_json=excluded.card_json,"
        "prompt_version=excluded.prompt_version,model=excluded.model,updated_at=excluded.updated_at,error=excluded.error",
        (
            card.folder_path,
            card.fingerprint,
            card.status,
            json.dumps(payload, ensure_ascii=False),
            card.prompt_version,
            card.model,
            card.updated_at,
            card.error,
        ),
    )


def run_ai_index(
    workspace_id: str,
    limit: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
    *,
    scope: Literal["all", "documents", "folders"] = "all",
    max_calls: int | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    if scope not in {"all", "documents", "folders"}:
        raise ValueError("Unknown AI index scope")
    workspace = get_workspace(workspace_id)
    store = WorkspaceStore(workspace)
    status = ai_index_status(workspace_id)
    requested_limit = max_calls if max_calls is not None else limit
    configured = _config_for(workspace)
    if requested_limit is not None:
        configured.ai_policy.max_calls_per_run = requested_limit
    else:
        configured.ai_policy.max_calls_per_run = max(
            1,
            status.estimated_calls
            + (status.failed_document_count + status.failed_folder_count if retry_failed else 0),
        )
    provider = create_provider(configured, require_network=True)
    completed = 0
    errors: list[str] = []
    stopped_early = False
    started = time.perf_counter()

    def usage_payload() -> dict[str, Any]:
        raw_usage = getattr(provider, "usage", None)
        usage = raw_usage if isinstance(raw_usage, AIUsage) else AIUsage()
        payload = usage.model_dump(mode="json")
        payload["duration_ms"] = max(
            int(payload.get("duration_ms", 0)),
            int((time.perf_counter() - started) * 1000),
        )
        return payload

    def report(phase: str, current: str = "", total: int = 0) -> None:
        if progress:
            usage = usage_payload()
            progress(
                {
                    "phase": phase,
                    "total": total,
                    "completed": completed,
                    "failed": len(errors),
                    "current_file": current,
                    "calls": usage["calls"],
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                    "total_tokens": usage["total_tokens"],
                    "duration_ms": usage["duration_ms"],
                    "estimated_cost": usage["estimated_cost"],
                    "cost_known": usage["estimated_cost"] is not None,
                }
            )

    with closing(store._connect()) as connection:
        _ensure_ai_schema(store, connection)
        document_rows = connection.execute(
            "SELECT document_id, content_hash FROM documents "
            "WHERE indexing_state != 'failed' ORDER BY relative_path"
        ).fetchall()
        pending_documents = []
        for row in document_rows:
            card = connection.execute(
                "SELECT content_hash, status FROM ai_document_cards WHERE document_id = ?",
                (str(row["document_id"]),),
            ).fetchone()
            if (
                card is None
                or str(card["content_hash"]) != str(row["content_hash"])
                or (retry_failed and str(card["status"]) == "failed")
            ):
                pending_documents.append(row)
        if scope == "folders":
            pending_documents = []
        folders = _folder_paths(store, connection)
        existing_folders = {
            str(row["folder_path"]): row
            for row in connection.execute("SELECT * FROM ai_folder_cards").fetchall()
        }
        pending_folders = [
            folder
            for folder in folders
            if folder not in existing_folders
            or str(existing_folders[folder]["fingerprint"])
            != _folder_fingerprint(connection, folder)
            or (retry_failed and str(existing_folders[folder]["status"]) == "failed")
        ]
        if scope == "documents":
            pending_folders = []
        total = len(pending_documents) + len(pending_folders)
        call_limit = total if requested_limit is None else min(total, max(0, requested_limit))
        pending_documents = pending_documents[:call_limit]
        report("documents", total=total)
        for row in pending_documents:
            document_id = str(row["document_id"])
            report("document", str(row["document_id"]), total)
            try:
                value = _document_input(connection, document_id)
                if value is None:
                    continue
                source, document = value
                summary: GeneratedSummary = provider.generate_leaf(document)
                card = AIDocumentCard(
                    document_id=document_id,
                    content_hash=str(source["content_hash"]),
                    one_sentence_summary=summary.one_sentence_summary,
                    description=summary.description,
                    tags=summary.tag_rough,
                    topic_keywords=summary.topic_keywords,
                    recommended_reading=summary.recommended_reading,
                    model=workspace.ai_policy.model,
                    updated_at=utc_now(),
                )
                _store_document_card(connection, card)
                completed += 1
            except Exception as error:
                errors.append(f"{document_id}: {type(error).__name__}")
                _store_document_card(
                    connection,
                    AIDocumentCard(
                        document_id=document_id,
                        content_hash=str(row["content_hash"]),
                        status="failed",
                        model=workspace.ai_policy.model,
                        updated_at=utc_now(),
                        error=str(error)[:500],
                    ),
                )
                connection.commit()
                if isinstance(
                    error,
                    (ProviderAuthError, ProviderQuotaError, ProviderRateLimitError),
                ):
                    stopped_early = True
                    break
            else:
                connection.commit()
        remaining = max(0, call_limit - len(pending_documents))
        if remaining and not stopped_early:
            for folder in sorted(
                pending_folders,
                key=lambda value: value.count("/"),
                reverse=True,
            )[:remaining]:
                report("folder", folder, total)
                fingerprint = _folder_fingerprint(connection, folder)
                prefix = "" if folder == "." else folder.rstrip("/") + "/"
                child_rows = connection.execute(
                    "SELECT d.name, d.relative_path, d.overview, c.card_json FROM documents d "
                    "LEFT JOIN ai_document_cards c ON c.document_id = d.document_id "
                    "WHERE d.relative_path LIKE ? ORDER BY d.relative_path LIMIT 500",
                    (f"{prefix}%",),
                ).fetchall()
                children = []
                for child in child_rows:
                    try:
                        card_json = json.loads(str(child["card_json"] or "{}"))
                    except ValueError:
                        card_json = {}
                    children.append(
                        {
                            "name": str(child["name"]),
                            "path": str(child["relative_path"]),
                            "one_sentence_summary": str(
                                card_json.get("one_sentence_summary") or child["overview"]
                            ),
                            "topic_keywords": card_json.get("topic_keywords", []),
                        }
                    )
                try:
                    summary = provider.summarize_folder(folder, children)
                    _store_folder_card(
                        connection,
                        AIFolderCard(
                            folder_path=folder,
                            fingerprint=fingerprint,
                            one_sentence_summary=summary.one_sentence_summary,
                            description=summary.description,
                            tags=summary.tag_rough,
                            topic_keywords=summary.topic_keywords,
                            recommended_reading=summary.recommended_reading,
                            model=workspace.ai_policy.model,
                            updated_at=utc_now(),
                        ),
                    )
                    completed += 1
                except Exception as error:
                    errors.append(f"{folder}: {type(error).__name__}")
                    _store_folder_card(
                        connection,
                        AIFolderCard(
                            folder_path=folder,
                            fingerprint=fingerprint,
                            status="failed",
                            model=workspace.ai_policy.model,
                            updated_at=utc_now(),
                            error=str(error)[:500],
                        ),
                    )
                    connection.commit()
                    if isinstance(
                        error,
                        (ProviderAuthError, ProviderQuotaError, ProviderRateLimitError),
                    ):
                        stopped_early = True
                        break
                else:
                    connection.commit()
        connection.execute(
            "INSERT INTO workspace_metadata(key, value) "
            "VALUES('ai_index_last_run', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (utc_now(),),
        )
        connection.execute(
            "INSERT INTO workspace_metadata(key, value) "
            "VALUES('ai_index_last_error', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("; ".join(errors)[:1000],),
        )
        connection.commit()
    report("completed", total=total)
    usage = usage_payload()
    return {
        "workspace_id": workspace_id,
        "completed": completed,
        "estimated": total,
        "failed": len(errors),
        "errors": errors,
        "stopped_early": stopped_early,
        "usage": usage,
        "cost_known": usage["estimated_cost"] is not None,
        "status": ai_index_status(workspace_id).model_dump(mode="json"),
    }


def _candidate_id(result: WorkspaceSearchResult, evidence_index: int) -> str:
    evidence = [result.best_evidence, *result.additional_evidence][evidence_index]
    locator = evidence.locator.model_dump_json() if evidence.locator else ""
    value = f"{result.document_id}|{evidence.page_number}|{locator}|{evidence.excerpt}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _candidates(results: list[WorkspaceSearchResult]) -> list[ResearchCandidate]:
    values: list[ResearchCandidate] = []
    for result in results[:50]:
        for index, evidence in enumerate([result.best_evidence, *result.additional_evidence][:3]):
            values.append(
                ResearchCandidate(
                    candidate_id=_candidate_id(result, index),
                    document_id=result.document_id,
                    content_hash=result.content_hash,
                    name=result.name,
                    relative_path=result.relative_path,
                    page_number=evidence.page_number,
                    locator=evidence.locator,
                    excerpt=evidence.excerpt,
                    reason=evidence.reason,
                    quality_score=evidence.quality_score,
                    source_ref=result.source_ref,
                    overview=result.overview,
                )
            )
    return values


def create_research_proposal(
    workspace_id: str,
    goal: str,
    title: str = "",
    template_id: TaskTemplateId = "free_research",
) -> ResearchTaskProposal:
    if template_id not in {"literature_review", "course_report", "free_research"}:
        raise ValueError(f"Unknown task template: {template_id}")
    workspace = get_workspace(workspace_id)
    store = WorkspaceStore(workspace)
    report = store.search(goal, limit=50, mode="local")
    candidates = _candidates(report.results)
    provider = create_provider(_config_for(workspace), require_network=True)
    payload = {
        "goal": goal,
        "template_id": template_id,
        "candidates": [item.model_dump(mode="json") for item in candidates],
    }
    output = provider._json_call("research_task", RESEARCH_TASK_PROMPT, payload)  # type: ignore[attr-defined]
    allowed = {item.candidate_id for item in candidates}
    slots: list[ResearchSlotProposal] = []
    selected_globally: set[str] = set()
    raw_slots = output.get("slots", [])
    if not isinstance(raw_slots, list):
        raw_slots = []
    for raw in raw_slots:
        if not isinstance(raw, dict):
            continue
        raw_candidate_ids = raw.get("candidate_ids", [])
        if not isinstance(raw_candidate_ids, list):
            raw_candidate_ids = []
        selected = [
            str(value)
            for value in raw_candidate_ids
            if str(value) in allowed and str(value) not in selected_globally
        ]
        selected_globally.update(selected)
        raw_rationales = raw.get("rationales", {})
        if not isinstance(raw_rationales, dict):
            raw_rationales = {}
        rationales = {
            str(key): str(value)[:2_000]
            for key, value in raw_rationales.items()
            if str(key) in selected
        }
        slots.append(
            ResearchSlotProposal(
                name=str(raw.get("name", "证据"))[:200] or "证据",
                description=str(raw.get("description", ""))[:1_000],
                required=bool(raw.get("required", False)),
                candidate_ids=list(dict.fromkeys(selected)),
                rationales=rationales,
            )
        )
    if not slots or not any(slot.candidate_ids for slot in slots):
        slots = [
            ResearchSlotProposal(
                name="核心证据",
                required=True,
                candidate_ids=[item.candidate_id for item in candidates[:5]],
            )
        ]
    used = {candidate_id for slot in slots for candidate_id in slot.candidate_ids}
    raw_warnings = output.get("warnings", [])
    if not isinstance(raw_warnings, list):
        raw_warnings = []
    warnings = [str(item) for item in raw_warnings if str(item)]
    raw_gaps = output.get("gaps", [])
    if not isinstance(raw_gaps, list):
        raw_gaps = []
    if len(used) < len(candidates):
        warnings.append(f"有 {len(candidates) - len(used)} 条候选证据未被任务提案采用。")
    return ResearchTaskProposal(
        workspace_id=workspace_id,
        template_id=template_id,
        title=(title.strip() or str(output.get("title", "研究资料包")).strip() or "研究资料包")[
            :200
        ],
        goal=goal.strip(),
        summary=str(output.get("summary", ""))[:4_000],
        warnings=warnings,
        gaps=[str(item) for item in raw_gaps if str(item)][:30],
        slots=slots,
        candidates=candidates,
    )


def _canonical_candidates(workspace_id: str, goal: str) -> dict[str, ResearchCandidate]:
    report = WorkspaceStore(get_workspace(workspace_id)).search(goal, limit=50, mode="local")
    return {item.candidate_id: item for item in _candidates(report.results)}


def _validate_proposal_bindings(
    workspace_id: str,
    proposal: ResearchTaskProposal,
    canonical: dict[str, ResearchCandidate],
) -> dict[str, ResearchCandidate]:
    if proposal.workspace_id != workspace_id:
        raise ValueError("Research proposal belongs to another workspace")
    if proposal.template_id not in {"literature_review", "course_report", "free_research"}:
        raise ValueError(f"Unknown task template: {proposal.template_id}")

    supplied: dict[str, ResearchCandidate] = {}
    for candidate in proposal.candidates:
        if candidate.candidate_id in supplied:
            raise ValueError("Research proposal contains duplicate candidates")
        server_candidate = canonical.get(candidate.candidate_id)
        if server_candidate is None:
            raise ValueError("Research proposal contains an unknown candidate")
        for field in (
            "document_id",
            "content_hash",
            "name",
            "relative_path",
            "page_number",
            "locator",
            "excerpt",
            "source_ref",
            "overview",
        ):
            if getattr(candidate, field) != getattr(server_candidate, field):
                raise ValueError("Research proposal candidate data no longer matches the workspace")
        supplied[candidate.candidate_id] = server_candidate

    selected: set[str] = set()
    for slot in proposal.slots:
        local: set[str] = set()
        for candidate_id in slot.candidate_ids:
            if candidate_id in local or candidate_id in selected:
                raise ValueError("Research proposal assigns a candidate more than once")
            if candidate_id not in supplied:
                raise ValueError(
                    "Research proposal references a candidate outside its candidate list"
                )
            local.add(candidate_id)
            selected.add(candidate_id)
    return supplied


def confirm_research_proposal(workspace_id: str, proposal: ResearchTaskProposal) -> WorkspaceTask:
    from .workspace_tasks_v2 import create_task, task_path

    canonical = _canonical_candidates(workspace_id, proposal.goal)
    candidates = _validate_proposal_bindings(workspace_id, proposal, canonical)
    task = create_task(workspace_id, proposal.title, proposal.goal, proposal.template_id)
    documents = {
        item.document_id: item
        for item in WorkspaceStore(get_workspace(workspace_id)).list_documents()
    }
    slots = [
        WorkspaceTaskSlot(
            slot_id=str(__import__("uuid").uuid4()),
            name=slot.name,
            description=slot.description,
            position=index,
            required=slot.required,
        )
        for index, slot in enumerate(proposal.slots)
    ] or task.slots
    items: list[WorkspaceTaskItem] = []
    for slot_index, slot in enumerate(proposal.slots):
        target_slot = slots[slot_index]
        for position, candidate_id in enumerate(slot.candidate_ids):
            candidate = candidates[candidate_id]
            document = documents.get(candidate.document_id)
            current = document is not None and document.content_hash == candidate.content_hash
            items.append(
                WorkspaceTaskItem(
                    item_id=str(__import__("uuid").uuid4()),
                    document_id=candidate.document_id,
                    content_hash=candidate.content_hash,
                    name=candidate.name,
                    relative_path=candidate.relative_path,
                    page_number=candidate.page_number,
                    excerpt=candidate.excerpt,
                    rationale=slot.rationales.get(candidate_id, candidate.reason),
                    slot_id=target_slot.slot_id,
                    review_state="pending",
                    source_status="resolved" if current else "source_unconfirmed",
                    source_ref=candidate.source_ref,
                    locator=candidate.locator,
                    freshness_status="unverified" if current else "missing",
                    position=position,
                )
            )
    task.slots = slots
    task.items = items
    task.lifecycle = "saved"
    task.schema_version = "2.1"
    try:
        _validate_task(task)
    except WorkspaceTaskError as error:
        raise ValueError(str(error)) from error
    atomic_write_json(task_path(workspace_id, task.task_id), task.model_dump(mode="json"))
    return task
