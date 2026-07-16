from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, cast

from packaging.version import Version
from pydantic import Field

from .citations import (
    DEFAULT_CITATION_STYLE,
    CitationRecord,
    CitationStyle,
    render_citation,
)
from .config import load_global_config, workspace_tasks_path
from .models import GlobalWorkspace, OctopusModel, utc_now
from .utils import atomic_write_json, atomic_write_text, load_json
from .workspace_sources import EvidenceLocator, SourceRef
from .workspace_v2 import WorkspaceDocument, WorkspaceStore

TASK_SCHEMA_VERSION = "2.1"
TaskTemplateId = Literal["literature_review", "course_report", "free_research"]
FreshnessStatus = Literal["current", "changed", "missing", "unverified"]

_WORKSPACE_TASKS_LOCK = threading.RLock()


class WorkspaceTaskError(RuntimeError):
    pass


class WorkspaceTaskNotFoundError(WorkspaceTaskError):
    pass


class WorkspaceTaskConflictError(WorkspaceTaskError):
    pass


class WorkspaceTaskVersionError(WorkspaceTaskError):
    pass


class WorkspaceTaskSlot(OctopusModel):
    slot_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1_000)
    position: int = Field(default=0, ge=0)
    required: bool = False


class WorkspaceTaskItem(OctopusModel):
    item_id: str
    document_id: str = ""
    content_hash: str = ""
    name: str = Field(min_length=1, max_length=500)
    relative_path: str = ""
    page_number: int | None = Field(default=None, ge=1)
    excerpt: str = Field(default="", max_length=4_000)
    rationale: str = Field(default="", max_length=2_000)
    slot_id: str
    review_state: Literal["confirmed", "pending"] = "confirmed"
    source_status: Literal["resolved", "source_unconfirmed"] = "resolved"
    source_ref: SourceRef | None = None
    locator: EvidenceLocator | None = None
    citation: CitationRecord | None = None
    verified_content_hash: str = ""
    verified_at: str = ""
    freshness_status: FreshnessStatus = "unverified"
    quality_flags: list[str] = Field(default_factory=list)
    error_code: str = ""
    position: int = Field(default=0, ge=0)
    added_at: str = Field(default_factory=utc_now)


class WorkspaceTask(OctopusModel):
    schema_version: str = TASK_SCHEMA_VERSION
    task_id: str
    workspace_id: str
    revision: int = Field(default=1, ge=1)
    lifecycle: Literal["draft", "saved", "archived"] = "draft"
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(default="", max_length=2_000)
    template_id: TaskTemplateId = "free_research"
    citation_style: CitationStyle = DEFAULT_CITATION_STYLE
    slots: list[WorkspaceTaskSlot] = Field(default_factory=list)
    items: list[WorkspaceTaskItem] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    migrated_from_v1: bool = False


class WorkspaceTaskSummary(OctopusModel):
    schema_version: str = TASK_SCHEMA_VERSION
    task_id: str
    workspace_id: str
    revision: int = 1
    lifecycle: str = "draft"
    title: str
    goal: str = ""
    item_count: int = 0
    pending_count: int = 0
    unresolved_count: int = 0
    stale_count: int = 0
    updated_at: str = ""
    writable: bool = True


_TEMPLATE_SLOTS: dict[TaskTemplateId, tuple[tuple[str, str, bool], ...]] = {
    "literature_review": (
        ("背景", "研究主题的背景、概念与问题范围。", True),
        ("核心文献", "最直接相关、需要重点阅读和引用的文献。", True),
        ("方法与数据", "研究方法、样本、数据和分析路径。", False),
        ("主要结论", "各来源的重要发现与可引用结论。", True),
        ("相反证据", "相互矛盾、限制结论或提供替代解释的证据。", False),
        ("研究缺口", "尚未解决的问题、限制与后续研究方向。", False),
    ),
    "course_report": (
        ("题目与要求", "课程要求、研究问题与交付约束。", True),
        ("核心观点", "报告拟论证的中心观点和分论点。", True),
        ("论据与材料", "支持观点的原文、数据、案例和图表。", True),
        ("方法与过程", "计算、实验、调研或分析过程。", False),
        ("结论", "可用于形成结论和建议的证据。", True),
        ("参考资料", "背景阅读和补充引用。", False),
    ),
    "free_research": (
        ("核心证据", "直接支持当前研究目标的页面或文本证据。", True),
        ("补充证据", "提供背景、上下文或旁证。", False),
        ("待核验", "来源或正文识别仍需人工确认。", False),
    ),
}


def task_template_slots(template_id: TaskTemplateId) -> list[WorkspaceTaskSlot]:
    return [
        WorkspaceTaskSlot(
            slot_id=str(uuid.uuid4()),
            name=name,
            description=description,
            position=position,
            required=required,
        )
        for position, (name, description, required) in enumerate(_TEMPLATE_SLOTS[template_id])
    ]


def list_task_templates() -> list[dict[str, Any]]:
    names: dict[TaskTemplateId, str] = {
        "literature_review": "文献综述",
        "course_report": "课程报告",
        "free_research": "自由研究",
    }
    return [
        {
            "template_id": template_id,
            "name": names[template_id],
            "slots": [
                {
                    "name": name,
                    "description": description,
                    "required": required,
                    "position": position,
                }
                for position, (name, description, required) in enumerate(slots)
            ],
        }
        for template_id, slots in _TEMPLATE_SLOTS.items()
    ]


def _default_slots() -> list[WorkspaceTaskSlot]:
    return task_template_slots("free_research")


def task_directory(workspace_id: str) -> Path:
    return workspace_tasks_path(workspace_id)


def task_path(workspace_id: str, task_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(task_id))
    except ValueError as error:
        raise WorkspaceTaskNotFoundError("Task not found") from error
    return task_directory(workspace_id) / f"{normalized}.json"


def _raw_payload(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise WorkspaceTaskError("Task is not a JSON object")
    return payload


def task_backup_path(path: Path, schema_version: str = "2.0") -> Path:
    safe_version = re.sub(r"[^0-9A-Za-z_.-]+", "_", schema_version) or "unknown"
    return path.with_name(f"{path.name}.v{safe_version}.bak")


def _legacy_source_ref(raw_item: dict[str, Any]) -> dict[str, Any] | None:
    relative_path = str(raw_item.get("relative_path", "")).replace("\\", "/")
    if not relative_path:
        return None
    return {
        "kind": "physical",
        "workspace_path": relative_path,
        "virtual_path": relative_path,
    }


def _legacy_locator(raw_item: dict[str, Any]) -> dict[str, Any] | None:
    page_number = raw_item.get("page_number")
    if isinstance(page_number, int) and page_number >= 1:
        return {"kind": "page", "page_number": page_number, "label": f"第 {page_number} 页"}
    if str(raw_item.get("excerpt", "")).strip():
        return {"kind": "document", "label": "文本摘录"}
    return None


def _migrate_task_payload(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    value = str(payload.get("schema_version", "0"))
    try:
        current = Version(value)
    except ValueError as error:
        raise WorkspaceTaskVersionError(f"Invalid task schema version: {value}") from error
    if current >= Version(TASK_SCHEMA_VERSION):
        return payload

    migrated = deepcopy(payload)
    migrated["schema_version"] = TASK_SCHEMA_VERSION
    migrated.setdefault("template_id", "free_research")
    migrated.setdefault("citation_style", DEFAULT_CITATION_STYLE)
    raw_items = migrated.get("items", [])
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            raw_item.setdefault("source_ref", _legacy_source_ref(raw_item))
            raw_item.setdefault("locator", _legacy_locator(raw_item))
            raw_item.setdefault("citation", None)
            raw_item.setdefault("quality_flags", [])
            raw_item.setdefault("error_code", "")
            resolved = raw_item.get("source_status", "resolved") == "resolved"
            confirmed = raw_item.get("review_state", "confirmed") == "confirmed"
            content_hash = str(raw_item.get("content_hash", ""))
            if resolved and confirmed and content_hash:
                raw_item.setdefault("verified_content_hash", content_hash)
                raw_item.setdefault(
                    "verified_at",
                    str(raw_item.get("added_at") or migrated.get("updated_at") or utc_now()),
                )
                raw_item.setdefault("freshness_status", "current")
            else:
                raw_item.setdefault("verified_content_hash", "")
                raw_item.setdefault("verified_at", "")
                raw_item.setdefault("freshness_status", "unverified")

    backup = task_backup_path(path, value)
    if not backup.exists():
        atomic_write_text(backup, path.read_text(encoding="utf-8-sig"))
    atomic_write_json(path, migrated)
    return migrated


def _task_payload(path: Path) -> dict[str, Any]:
    payload = _raw_payload(path)
    return _migrate_task_payload(path, payload)


def _ensure_supported(payload: dict[str, Any]) -> None:
    value = str(payload.get("schema_version", "0"))
    try:
        newer = Version(value) > Version(TASK_SCHEMA_VERSION)
    except ValueError as error:
        raise WorkspaceTaskVersionError(f"Invalid task schema version: {value}") from error
    if newer:
        raise WorkspaceTaskVersionError(
            f"Task schema {value} is newer than supported {TASK_SCHEMA_VERSION}"
        )


def _validate_task(task: WorkspaceTask) -> None:
    slot_ids = [slot.slot_id for slot in task.slots]
    if not slot_ids:
        raise WorkspaceTaskError("Task requires at least one slot")
    if len(slot_ids) != len(set(slot_ids)):
        raise WorkspaceTaskError("Task contains duplicate slot IDs")
    item_ids = [item.item_id for item in task.items]
    if len(item_ids) != len(set(item_ids)):
        raise WorkspaceTaskError("Task contains duplicate item IDs")
    resolved_keys = [
        (
            item.document_id,
            item.source_ref.virtual_path if item.source_ref is not None else item.relative_path,
            item.locator.model_dump_json() if item.locator is not None else item.page_number,
            item.excerpt,
        )
        for item in task.items
        if item.source_status == "resolved"
    ]
    if len(resolved_keys) != len(set(resolved_keys)):
        raise WorkspaceTaskError("Task contains duplicate evidence items")
    allowed_slots = set(slot_ids)
    if any(item.slot_id not in allowed_slots for item in task.items):
        raise WorkspaceTaskError("Task item references an unknown slot")
    if task.template_id not in _TEMPLATE_SLOTS:
        raise WorkspaceTaskError("Task references an unknown template")


def _set_confirmed_metadata(
    task: WorkspaceTask,
    *,
    previous: WorkspaceTask | None = None,
) -> None:
    previous_items = {item.item_id: item for item in previous.items} if previous else {}
    for item in task.items:
        old = previous_items.get(item.item_id)
        if item.source_status != "resolved":
            if item.freshness_status == "current":
                item.freshness_status = "unverified"
            continue
        if item.review_state == "confirmed":
            if (
                not item.verified_content_hash
                or old is None
                or old.review_state != "confirmed"
                or old.verified_content_hash != item.content_hash
            ):
                item.verified_content_hash = item.content_hash
                item.verified_at = utc_now()
            item.freshness_status = "current"
        elif not item.verified_content_hash:
            item.freshness_status = "unverified"


def create_task(
    workspace_id: str,
    title: str,
    goal: str = "",
    template_id: TaskTemplateId = "free_research",
) -> WorkspaceTask:
    if template_id not in _TEMPLATE_SLOTS:
        raise WorkspaceTaskError(f"Unknown task template: {template_id}")
    task = WorkspaceTask(
        task_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        title=title.strip() or "未命名资料包",
        goal=goal.strip(),
        template_id=template_id,
        slots=task_template_slots(template_id),
    )
    _validate_task(task)
    with _WORKSPACE_TASKS_LOCK:
        atomic_write_json(task_path(workspace_id, task.task_id), task.model_dump(mode="json"))
    return task


def _current_documents(workspace_id: str) -> list[WorkspaceDocument] | None:
    workspace = load_global_config().workspaces.get(workspace_id)
    if workspace is None:
        return None
    try:
        return WorkspaceStore(workspace).list_documents()
    except (OSError, sqlite3.Error):
        return None


def _source_kind(source_ref: SourceRef | None) -> str:
    if source_ref is None:
        return "physical"
    value = getattr(source_ref, "kind", None)
    if value is None:
        value = getattr(source_ref, "source_kind", "physical")
    return str(value)


def _document_source_ref(document: WorkspaceDocument) -> SourceRef:
    if document.source_ref is not None:
        return document.source_ref
    return SourceRef(
        kind="physical",
        workspace_path=document.relative_path,
        virtual_path=document.relative_path,
    )


def _item_source_ref(item: WorkspaceTaskItem) -> SourceRef | None:
    if item.source_ref is not None:
        return item.source_ref
    if not item.relative_path:
        return None
    return SourceRef(
        kind="physical",
        workspace_path=item.relative_path,
        virtual_path=item.relative_path,
    )


def _normalized_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _same_source_scope(
    item_ref: SourceRef | None,
    document_ref: SourceRef,
    *,
    require_member: bool = True,
) -> bool:
    item_kind = _source_kind(item_ref)
    document_kind = _source_kind(document_ref)
    if item_kind != document_kind:
        return False
    if item_kind != "archive_member":
        return True
    if item_ref is None:
        return False
    item_stable = str(getattr(item_ref, "stable_id", ""))
    document_stable = str(getattr(document_ref, "stable_id", ""))
    if require_member and item_stable and document_stable:
        return item_stable == document_stable
    item_container = _normalized_path(
        str(getattr(item_ref, "container_path", "") or item_ref.workspace_path)
    )
    document_container = _normalized_path(
        str(getattr(document_ref, "container_path", "") or document_ref.workspace_path)
    )
    if item_container.casefold() != document_container.casefold():
        return False
    if not require_member:
        return True
    item_chain = tuple(getattr(item_ref, "member_chain", []) or [])
    document_chain = tuple(getattr(document_ref, "member_chain", []) or [])
    if item_chain and document_chain:
        return item_chain == document_chain
    return _normalized_path(item_ref.member_path) == _normalized_path(document_ref.member_path)


def _same_source_path(item: WorkspaceTaskItem, document: WorkspaceDocument) -> bool:
    item_ref = _item_source_ref(item)
    document_ref = _document_source_ref(document)
    if not _same_source_scope(item_ref, document_ref):
        return False
    if _source_kind(item_ref) == "archive_member":
        return True
    item_path = _normalized_path(
        item_ref.virtual_path if item_ref is not None else item.relative_path
    )
    document_path = _normalized_path(document_ref.virtual_path or document.relative_path)
    return item_path.casefold() == document_path.casefold()


def _matching_document(
    item: WorkspaceTaskItem,
    documents: list[WorkspaceDocument],
) -> tuple[WorkspaceDocument | None, FreshnessStatus]:
    by_id = {document.document_id: document for document in documents}
    item_ref = _item_source_ref(item)
    document = by_id.get(item.document_id)
    if document is not None and not _same_source_scope(item_ref, _document_source_ref(document)):
        document = None

    expected_hash = item.verified_content_hash or item.content_hash
    if document is not None:
        if expected_hash and document.content_hash != expected_hash:
            return None, "changed"
        return document, "current" if expected_hash else "unverified"

    path_matches = [document for document in documents if _same_source_path(item, document)]
    if len(path_matches) == 1:
        path_match = path_matches[0]
        if expected_hash and path_match.content_hash != expected_hash:
            return None, "changed"
        return path_match, "current" if expected_hash else "unverified"

    if _source_kind(item_ref) == "archive_member":
        # Archive member hashes are intentionally scoped to their container.
        scoped = [
            document
            for document in documents
            if _same_source_scope(
                item_ref,
                _document_source_ref(document),
                require_member=False,
            )
        ]
        hash_matches = [
            document
            for document in scoped
            if expected_hash and document.content_hash == expected_hash
        ]
        if len(hash_matches) == 1 and _same_source_path(item, hash_matches[0]):
            return hash_matches[0], "current"
        return None, "missing"

    hash_matches = [
        document
        for document in documents
        if expected_hash
        and document.content_hash == expected_hash
        and _source_kind(_document_source_ref(document)) != "archive_member"
    ]
    if len(hash_matches) == 1:
        return hash_matches[0], "current"
    return None, "missing"


def _apply_document_to_item(
    item: WorkspaceTaskItem,
    document: WorkspaceDocument,
    freshness: FreshnessStatus,
) -> None:
    item.document_id = document.document_id
    item.content_hash = document.content_hash
    item.name = document.name
    item.relative_path = document.relative_path
    item.source_ref = _document_source_ref(document)
    item.source_status = "resolved"
    item.freshness_status = freshness
    item.quality_flags = list(getattr(document, "quality_flags", []))
    item.error_code = str(getattr(document, "error_code", ""))
    document_locator = getattr(document, "locator", None)
    if item.locator is None and document_locator is not None:
        item.locator = document_locator


def _reconfirm_task_sources(
    task: WorkspaceTask,
    documents: list[WorkspaceDocument] | None = None,
) -> WorkspaceTask:
    current_documents = _current_documents(task.workspace_id) if documents is None else documents
    if current_documents is None:
        return task
    refreshed = task.model_copy(deep=True)
    for item in refreshed.items:
        document, freshness = _matching_document(item, current_documents)
        if document is None:
            item.source_status = "source_unconfirmed"
            item.review_state = "pending"
            item.freshness_status = freshness
            continue
        _apply_document_to_item(item, document, freshness)
        if item.review_state == "confirmed" and not item.verified_content_hash:
            item.verified_content_hash = document.content_hash
            item.verified_at = item.verified_at or utc_now()
            item.freshness_status = "current"
    return refreshed


def _load_task_unlocked(
    workspace_id: str,
    task_id: str,
    *,
    documents: list[WorkspaceDocument] | None = None,
) -> WorkspaceTask:
    path = task_path(workspace_id, task_id)
    if not path.exists():
        raise WorkspaceTaskNotFoundError("Task not found")
    payload = _raw_payload(path)
    _ensure_supported(payload)
    task = WorkspaceTask.model_validate(payload)
    if task.workspace_id != workspace_id:
        raise WorkspaceTaskNotFoundError("Task not found")
    _validate_task(task)
    return _reconfirm_task_sources(task, documents)


def load_task(workspace_id: str, task_id: str) -> WorkspaceTask:
    with _WORKSPACE_TASKS_LOCK:
        return _load_task_unlocked(workspace_id, task_id)


def _save_task_unlocked(
    workspace_id: str,
    task_id: str,
    expected_revision: int,
    replacement: WorkspaceTask,
    *,
    documents: list[WorkspaceDocument] | None = None,
    current: WorkspaceTask | None = None,
) -> WorkspaceTask:
    if documents is None:
        documents = _current_documents(workspace_id)
    if current is None:
        current = _load_task_unlocked(workspace_id, task_id, documents=documents)
    if replacement.workspace_id != workspace_id:
        raise WorkspaceTaskError("Task belongs to another workspace")
    if replacement.task_id != task_id:
        raise WorkspaceTaskError("Task ID cannot be changed")
    if current.revision != expected_revision:
        raise WorkspaceTaskConflictError(
            f"Task revision changed from {expected_revision} to {current.revision}"
        )
    saved = _reconfirm_task_sources(replacement.model_copy(deep=True), documents)
    _set_confirmed_metadata(saved, previous=current)
    saved.schema_version = TASK_SCHEMA_VERSION
    saved.revision = current.revision + 1
    saved.created_at = current.created_at
    saved.updated_at = utc_now()
    if saved.lifecycle == "draft":
        saved.lifecycle = "saved"
    _validate_task(saved)
    atomic_write_json(task_path(workspace_id, task_id), saved.model_dump(mode="json"))
    return saved


def save_task(
    workspace_id: str,
    task_id: str,
    expected_revision: int,
    replacement: WorkspaceTask,
) -> WorkspaceTask:
    with _WORKSPACE_TASKS_LOCK:
        return _save_task_unlocked(
            workspace_id,
            task_id,
            expected_revision,
            replacement,
        )


def archive_task(workspace_id: str, task_id: str, expected_revision: int) -> WorkspaceTask:
    with _WORKSPACE_TASKS_LOCK:
        documents = _current_documents(workspace_id)
        current = _load_task_unlocked(workspace_id, task_id, documents=documents)
        replacement = current.model_copy(deep=True)
        replacement.lifecycle = "archived"
        return _save_task_unlocked(
            workspace_id,
            task_id,
            expected_revision,
            replacement,
            documents=documents,
            current=current,
        )


def list_tasks(
    workspace_id: str,
    *,
    include_archived: bool = False,
) -> list[WorkspaceTaskSummary]:
    with _WORKSPACE_TASKS_LOCK:
        summaries: list[WorkspaceTaskSummary] = []
        directory = task_directory(workspace_id)
        if not directory.exists():
            return []
        documents = _current_documents(workspace_id)
        for path in sorted(directory.glob("*.json")):
            try:
                payload = _raw_payload(path)
                if str(payload.get("workspace_id", "")) != workspace_id:
                    continue
                schema_version = str(payload.get("schema_version", "0"))
                writable = Version(schema_version) <= Version(TASK_SCHEMA_VERSION)
                lifecycle: str
                title: str
                goal: str
                revision: int
                updated_at: str
                if writable:
                    payload = _migrate_task_payload(path, payload)
                    schema_version = str(payload.get("schema_version", TASK_SCHEMA_VERSION))
                    task = WorkspaceTask.model_validate(payload)
                    _validate_task(task)
                    if documents is not None:
                        task = _reconfirm_task_sources(task, documents)
                    lifecycle = task.lifecycle
                    title = task.title
                    goal = task.goal
                    revision = task.revision
                    updated_at = task.updated_at
                    item_count = len(task.items)
                    pending_count = sum(item.review_state == "pending" for item in task.items)
                    unresolved_count = sum(
                        item.source_status == "source_unconfirmed" for item in task.items
                    )
                    stale_count = sum(
                        item.freshness_status in {"changed", "missing"} for item in task.items
                    )
                else:
                    lifecycle = str(payload.get("lifecycle", "draft"))
                    title = str(payload.get("title", "未命名任务"))
                    goal = str(payload.get("goal", ""))
                    revision = int(str(payload.get("revision", 1)))
                    updated_at = str(payload.get("updated_at", ""))
                    raw_items = payload.get("items", [])
                    items = raw_items if isinstance(raw_items, list) else []
                    item_count = len(items)
                    pending_count = sum(
                        isinstance(item, dict) and item.get("review_state") == "pending"
                        for item in items
                    )
                    unresolved_count = sum(
                        isinstance(item, dict) and item.get("source_status") == "source_unconfirmed"
                        for item in items
                    )
                    stale_count = sum(
                        isinstance(item, dict)
                        and item.get("freshness_status") in {"changed", "missing"}
                        for item in items
                    )
                if lifecycle == "archived" and not include_archived:
                    continue
                summaries.append(
                    WorkspaceTaskSummary(
                        schema_version=schema_version,
                        task_id=str(payload.get("task_id", path.stem)),
                        workspace_id=workspace_id,
                        revision=revision,
                        lifecycle=lifecycle,
                        title=title,
                        goal=goal,
                        item_count=item_count,
                        pending_count=pending_count,
                        unresolved_count=unresolved_count,
                        stale_count=stale_count,
                        updated_at=updated_at,
                        writable=writable,
                    )
                )
            except (OSError, ValueError, WorkspaceTaskError):
                continue
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)


def _task_source_label(item: WorkspaceTaskItem) -> str:
    if item.source_ref is not None and item.source_ref.virtual_path:
        return item.source_ref.virtual_path
    return item.relative_path or item.name


def _task_locator_label(item: WorkspaceTaskItem) -> str:
    locator = item.locator
    if locator is None:
        return f"第 {item.page_number} 页" if item.page_number else ""
    if locator.label:
        return locator.label
    if locator.kind == "page" and locator.page_number:
        return f"第 {locator.page_number} 页"
    if locator.kind == "paragraph" and locator.paragraph_index:
        return f"第 {locator.paragraph_index} 段"
    if locator.kind == "table" and locator.table_index:
        return f"表格 {locator.table_index}"
    if locator.kind == "sheet":
        sheet = f"Sheet {locator.sheet_name}" if locator.sheet_name else "Sheet"
        return f"{sheet} {locator.cell_range}".strip()
    if locator.kind == "slide" and locator.slide_number:
        return f"幻灯片 {locator.slide_number}"
    if locator.kind == "image":
        return locator.label or "图片 OCR"
    if locator.kind == "text_line":
        if locator.line_start and locator.line_end and locator.line_end != locator.line_start:
            return f"第 {locator.line_start}-{locator.line_end} 行"
        if locator.line_start:
            return f"第 {locator.line_start} 行"
    return ""


def _task_item_status(item: WorkspaceTaskItem) -> str:
    if item.source_status == "source_unconfirmed" or item.freshness_status == "missing":
        return "来源缺失，待重新核验"
    if item.freshness_status == "changed":
        return "来源已变化，待重新核验"
    if item.review_state == "pending" or item.freshness_status == "unverified":
        return "待人工核验"
    return "已确认"


def render_task_markdown(task: WorkspaceTask) -> str:
    task = _reconfirm_task_sources(task)
    items_by_slot: dict[str, list[WorkspaceTaskItem]] = {slot.slot_id: [] for slot in task.slots}
    for item in task.items:
        items_by_slot.setdefault(item.slot_id, []).append(item)
    lines = [f"# {task.title}", ""]
    if task.goal:
        lines.extend([task.goal, ""])
    lines.extend(
        [
            f"> Octopus 资料包 · {len(task.items)} 条证据 · revision {task.revision}",
            "",
        ]
    )
    for slot in sorted(task.slots, key=lambda value: value.position):
        lines.extend([f"## {slot.name}", ""])
        if slot.description:
            lines.extend([slot.description, ""])
        items = sorted(items_by_slot.get(slot.slot_id, []), key=lambda value: value.position)
        if not items:
            lines.extend(["- 暂无证据。", ""])
            continue
        for item in items:
            source = _task_source_label(item).replace("`", "'")
            locator = _task_locator_label(item)
            location = f" · {locator}" if locator else ""
            source_status = (
                "来源待重新确认"
                if item.source_status == "source_unconfirmed"
                else "已定位"
            )
            review_status = "待核验" if item.review_state == "pending" else "已确认"
            status_detail = _task_item_status(item)
            detail = "" if status_detail == "待人工核验" else f" · {status_detail}"
            lines.append(
                f"- **{item.name}** · {source_status}{detail} · "
                f"人工核验：{review_status}{location} · `{source}`"
            )
            if item.excerpt:
                lines.append(f"  - 证据：{item.excerpt}")
            if item.rationale:
                lines.append(f"  - 用途：{item.rationale}")
            if item.citation is not None:
                lines.append(
                    f"  - 引用：{render_citation(item.citation, task.citation_style)}"
                )
            if item.quality_flags:
                lines.append(f"  - 解析提示：{', '.join(item.quality_flags)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _normalized_content_hash(value: object) -> str:
    content_id = str(value or "").strip()
    if content_id.casefold().startswith("sha256:"):
        return content_id.split(":", 1)[1]
    return content_id


def _legacy_lifecycle(value: object) -> Literal["draft", "saved", "archived"]:
    text = str(value)
    if text in {"draft", "saved", "archived"}:
        return cast(Literal["draft", "saved", "archived"], text)
    return "saved"


def _legacy_page_and_excerpt(item: dict[str, Any]) -> tuple[int | None, str]:
    raw_anchors = item.get("anchors", [])
    anchors = raw_anchors if isinstance(raw_anchors, list) else []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        locator = str(anchor.get("locator", ""))
        match = re.search(r"(?:第\s*)?(\d+)\s*页|page\s*[:#]?\s*(\d+)", locator, re.I)
        page = int(next(value for value in match.groups() if value)) if match else None
        excerpt = str(anchor.get("text_excerpt", "")).strip()
        if page is not None or excerpt:
            return page, excerpt
    return None, ""


def _document_maps(
    documents: list[WorkspaceDocument],
) -> tuple[dict[str, list[WorkspaceDocument]], dict[str, WorkspaceDocument]]:
    by_hash: dict[str, list[WorkspaceDocument]] = {}
    by_path: dict[str, WorkspaceDocument] = {}
    for document in documents:
        by_hash.setdefault(document.content_hash, []).append(document)
        by_path[document.relative_path.casefold()] = document
    return by_hash, by_path


def _migrate_item(
    raw: dict[str, Any],
    slot_id: str,
    by_hash: dict[str, list[WorkspaceDocument]],
    by_path: dict[str, WorkspaceDocument],
) -> WorkspaceTaskItem:
    content_hash = _normalized_content_hash(raw.get("content_id"))
    relative_path = str(raw.get("raw_relative_path", "")).replace("\\", "/")
    hash_matches = by_hash.get(content_hash, []) if content_hash else []
    path_match = by_path.get(relative_path.casefold())
    document: WorkspaceDocument | None
    if len(hash_matches) == 1:
        document = hash_matches[0]
    elif content_hash:
        document = (
            path_match
            if path_match is not None and path_match.content_hash == content_hash
            else None
        )
    else:
        document = path_match
    page_number, excerpt = _legacy_page_and_excerpt(raw)
    resolved = document is not None
    return WorkspaceTaskItem(
        item_id=str(raw.get("item_id") or uuid.uuid4()),
        document_id=document.document_id if document else "",
        content_hash=document.content_hash if document else content_hash,
        name=str(raw.get("name") or (document.name if document else "未命名资料")),
        relative_path=document.relative_path if document else relative_path,
        page_number=page_number,
        excerpt=excerpt,
        rationale=str(raw.get("rationale", "")),
        slot_id=slot_id,
        review_state="confirmed" if raw.get("review_state") == "confirmed" else "pending",
        source_status="resolved" if resolved else "source_unconfirmed",
        position=int(str(raw.get("position", 0))),
        added_at=str(raw.get("added_at") or utc_now()),
    )


def migrate_legacy_tasks(workspace: GlobalWorkspace) -> dict[str, int]:
    with _WORKSPACE_TASKS_LOCK:
        legacy_index = Path(workspace.legacy_index_path) if workspace.legacy_index_path else None
        legacy_directory = legacy_index / ".octopus" / "task-packs" if legacy_index else None
        if legacy_directory is None or not legacy_directory.is_dir():
            return {"migrated": 0, "skipped": 0, "unresolved": 0}
        documents = WorkspaceStore(workspace).list_documents()
        by_hash, by_path = _document_maps(documents)
        migrated = 0
        skipped = 0
        unresolved = 0
        for source in sorted(legacy_directory.glob("*.json")):
            try:
                payload = _raw_payload(source)
                task_id = str(uuid.UUID(str(payload.get("task_pack_id", source.stem))))
            except (OSError, ValueError, WorkspaceTaskError):
                skipped += 1
                continue
            destination = task_path(workspace.workspace_id, task_id)
            if destination.exists():
                skipped += 1
                continue
            raw_slots = payload.get("slots", [])
            slots = [
                WorkspaceTaskSlot.model_validate(slot)
                for slot in (raw_slots if isinstance(raw_slots, list) else [])
                if isinstance(slot, dict)
            ]
            if not slots:
                slots = _default_slots()
            slot_ids = {slot.slot_id for slot in slots}
            fallback_slot = next(
                (slot.slot_id for slot in slots if slot.name == "待核验"),
                slots[-1].slot_id,
            )
            raw_items = payload.get("items", [])
            items: list[WorkspaceTaskItem] = []
            for raw_item in raw_items if isinstance(raw_items, list) else []:
                if not isinstance(raw_item, dict):
                    continue
                requested_slot = str(raw_item.get("slot_id", ""))
                item = _migrate_item(
                    raw_item,
                    requested_slot if requested_slot in slot_ids else fallback_slot,
                    by_hash,
                    by_path,
                )
                if item.source_status == "source_unconfirmed":
                    item.slot_id = fallback_slot
                    item.review_state = "pending"
                    unresolved += 1
                items.append(item)
            task = WorkspaceTask(
                task_id=task_id,
                workspace_id=workspace.workspace_id,
                revision=max(1, int(str(payload.get("revision", 1)))),
                lifecycle=_legacy_lifecycle(payload.get("lifecycle", "saved")),
                title=str(payload.get("title", "迁移任务")),
                goal=str(payload.get("goal", "")),
                slots=slots,
                items=items,
                created_at=str(payload.get("created_at") or utc_now()),
                updated_at=str(payload.get("updated_at") or utc_now()),
                migrated_from_v1=True,
            )
            _validate_task(task)
            atomic_write_json(destination, task.model_dump(mode="json"))
            migrated += 1
        return {"migrated": migrated, "skipped": skipped, "unresolved": unresolved}
