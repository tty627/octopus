from __future__ import annotations

import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from packaging.version import Version
from pydantic import Field

from .config import load_global_config, workspace_tasks_path
from .models import GlobalWorkspace, OctopusModel, utc_now
from .utils import atomic_write_json, load_json
from .workspace_v2 import WorkspaceDocument, WorkspaceStore

TASK_SCHEMA_VERSION = "2.0"

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
    updated_at: str = ""
    writable: bool = True


def _default_slots() -> list[WorkspaceTaskSlot]:
    return [
        WorkspaceTaskSlot(
            slot_id=str(uuid.uuid4()),
            name="核心证据",
            description="直接支持当前任务的页面或文本证据。",
            position=0,
            required=True,
        ),
        WorkspaceTaskSlot(
            slot_id=str(uuid.uuid4()),
            name="补充证据",
            description="提供背景、上下文或旁证。",
            position=1,
        ),
        WorkspaceTaskSlot(
            slot_id=str(uuid.uuid4()),
            name="待核验",
            description="来源或正文识别仍需人工确认。",
            position=2,
        ),
    ]


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
        (item.document_id, item.page_number, item.excerpt)
        for item in task.items
        if item.source_status == "resolved"
    ]
    if len(resolved_keys) != len(set(resolved_keys)):
        raise WorkspaceTaskError("Task contains duplicate evidence items")
    allowed_slots = set(slot_ids)
    if any(item.slot_id not in allowed_slots for item in task.items):
        raise WorkspaceTaskError("Task item references an unknown slot")


def create_task(
    workspace_id: str,
    title: str,
    goal: str = "",
) -> WorkspaceTask:
    task = WorkspaceTask(
        task_id=str(uuid.uuid4()),
        workspace_id=workspace_id,
        title=title.strip() or "未命名任务",
        goal=goal.strip(),
        slots=_default_slots(),
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


def _reconfirm_task_sources(
    task: WorkspaceTask,
    documents: list[WorkspaceDocument] | None = None,
) -> WorkspaceTask:
    current_documents = _current_documents(task.workspace_id) if documents is None else documents
    if current_documents is None:
        return task
    by_id = {document.document_id: document for document in current_documents}
    by_hash, by_path = _document_maps(current_documents)
    refreshed = task.model_copy(deep=True)
    for item in refreshed.items:
        document = by_id.get(item.document_id)
        if document is None and item.content_hash:
            matches = by_hash.get(item.content_hash, [])
            if len(matches) == 1:
                document = matches[0]
            elif item.relative_path:
                path_match = by_path.get(item.relative_path.casefold())
                if path_match is not None and path_match.content_hash == item.content_hash:
                    document = path_match
        if document is None and not item.content_hash and item.relative_path:
            document = by_path.get(item.relative_path.casefold())
        if document is None or (
            bool(item.content_hash) and document.content_hash != item.content_hash
        ):
            item.source_status = "source_unconfirmed"
            item.review_state = "pending"
            continue
        item.document_id = document.document_id
        item.content_hash = document.content_hash
        item.name = document.name
        item.relative_path = document.relative_path
        item.source_status = "resolved"
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
                    pending_count = sum(
                        item.review_state == "pending" for item in task.items
                    )
                    unresolved_count = sum(
                        item.source_status == "source_unconfirmed" for item in task.items
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
                        isinstance(item, dict)
                        and item.get("source_status") == "source_unconfirmed"
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
                        updated_at=updated_at,
                        writable=writable,
                    )
                )
            except (OSError, ValueError, WorkspaceTaskError):
                continue
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)


def render_task_markdown(task: WorkspaceTask) -> str:
    task = _reconfirm_task_sources(task)
    items_by_slot: dict[str, list[WorkspaceTaskItem]] = {
        slot.slot_id: [] for slot in task.slots
    }
    for item in task.items:
        items_by_slot.setdefault(item.slot_id, []).append(item)
    lines = [f"# {task.title}", ""]
    if task.goal:
        lines.extend([task.goal, ""])
    lines.extend([f"> Octopus 任务 · {len(task.items)} 条证据 · revision {task.revision}", ""])
    for slot in sorted(task.slots, key=lambda value: value.position):
        lines.extend([f"## {slot.name}", ""])
        if slot.description:
            lines.extend([slot.description, ""])
        items = sorted(items_by_slot.get(slot.slot_id, []), key=lambda value: value.position)
        if not items:
            lines.extend(["- 暂无证据。", ""])
            continue
        for item in items:
            source = item.relative_path or item.name
            status = "来源待重新确认" if item.source_status == "source_unconfirmed" else "已定位"
            review_status = (
                "待核验"
                if item.source_status == "source_unconfirmed"
                or item.review_state == "pending"
                else "已确认"
            )
            page = f" · 第 {item.page_number} 页" if item.page_number else ""
            lines.append(
                f"- **{item.name}** · {status} · 人工核验：{review_status}{page} · `{source}`"
            )
            if item.excerpt:
                lines.append(f"  - 证据：{item.excerpt}")
            if item.rationale:
                lines.append(f"  - 用途：{item.rationale}")
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
