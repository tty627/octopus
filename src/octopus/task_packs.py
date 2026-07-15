from __future__ import annotations

import uuid
from pathlib import Path

from packaging.version import Version

from .config import octopus_dir
from .models import (
    TaskPack,
    TaskPackItem,
    TaskPackSlot,
    TaskPackSummary,
    utc_now,
)
from .utils import atomic_write_json, load_json

TASK_PACK_SCHEMA_VERSION = "1.0"


class TaskPackError(RuntimeError):
    pass


class TaskPackNotFoundError(TaskPackError):
    pass


class TaskPackConflictError(TaskPackError):
    pass


class TaskPackVersionError(TaskPackError):
    pass


def task_packs_directory(index_repository: Path) -> Path:
    return octopus_dir(index_repository) / "task-packs"


def task_pack_path(index_repository: Path, task_pack_id: str) -> Path:
    try:
        normalized = str(uuid.UUID(task_pack_id))
    except ValueError as error:
        raise TaskPackNotFoundError("Task pack not found") from error
    return task_packs_directory(index_repository) / f"{normalized}.json"


def _default_slots() -> list[TaskPackSlot]:
    return [
        TaskPackSlot(
            slot_id=str(uuid.uuid4()),
            name="核心资料",
            description="直接支持当前任务的主要来源。",
            position=0,
            required=True,
        ),
        TaskPackSlot(
            slot_id=str(uuid.uuid4()),
            name="补充资料",
            description="提供背景、上下文或旁证的来源。",
            position=1,
        ),
        TaskPackSlot(
            slot_id=str(uuid.uuid4()),
            name="待核验",
            description="相关但存在版本、状态或提取质量风险的来源。",
            position=2,
        ),
    ]


def _raw_payload(path: Path) -> dict[str, object]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise TaskPackError("Task pack is not a JSON object")
    return payload


def _ensure_supported(payload: dict[str, object]) -> None:
    value = str(payload.get("schema_version", "0"))
    try:
        newer = Version(value) > Version(TASK_PACK_SCHEMA_VERSION)
    except ValueError as error:
        raise TaskPackVersionError(f"Invalid task pack schema version: {value}") from error
    if newer:
        raise TaskPackVersionError(
            f"Task pack schema {value} is newer than supported {TASK_PACK_SCHEMA_VERSION}"
        )


def _validate_pack(pack: TaskPack) -> None:
    slot_ids = [slot.slot_id for slot in pack.slots]
    if len(slot_ids) != len(set(slot_ids)):
        raise TaskPackError("Task pack contains duplicate slot IDs")
    if not slot_ids:
        raise TaskPackError("Task pack requires at least one slot")
    item_ids = [item.item_id for item in pack.items]
    if len(item_ids) != len(set(item_ids)):
        raise TaskPackError("Task pack contains duplicate item IDs")
    node_ids = [item.node_id for item in pack.items]
    if len(node_ids) != len(set(node_ids)):
        raise TaskPackError("Task pack contains duplicate source nodes")
    allowed_slots = set(slot_ids)
    if any(item.slot_id not in allowed_slots for item in pack.items):
        raise TaskPackError("Task pack item references an unknown slot")


def create_task_pack(
    index_repository: Path,
    repository_id: str,
    title: str,
    goal: str = "",
) -> TaskPack:
    task_pack_id = str(uuid.uuid4())
    pack = TaskPack(
        task_pack_id=task_pack_id,
        repository_id=repository_id,
        title=title.strip() or "未命名任务",
        goal=goal.strip(),
        slots=_default_slots(),
    )
    _validate_pack(pack)
    atomic_write_json(
        task_pack_path(index_repository, task_pack_id),
        pack.model_dump(mode="json"),
    )
    return pack


def load_task_pack(index_repository: Path, task_pack_id: str) -> TaskPack:
    path = task_pack_path(index_repository, task_pack_id)
    if not path.exists():
        raise TaskPackNotFoundError("Task pack not found")
    payload = _raw_payload(path)
    _ensure_supported(payload)
    pack = TaskPack.model_validate(payload)
    _validate_pack(pack)
    return pack


def save_task_pack(
    index_repository: Path,
    task_pack_id: str,
    repository_id: str,
    expected_revision: int,
    replacement: TaskPack,
) -> TaskPack:
    current = load_task_pack(index_repository, task_pack_id)
    if current.repository_id != repository_id or replacement.repository_id != repository_id:
        raise TaskPackError("Task pack belongs to another repository")
    if replacement.task_pack_id != task_pack_id:
        raise TaskPackError("Task pack ID cannot be changed")
    if current.revision != expected_revision:
        raise TaskPackConflictError(
            f"Task pack revision changed from {expected_revision} to {current.revision}"
        )
    replacement.schema_version = TASK_PACK_SCHEMA_VERSION
    replacement.revision = current.revision + 1
    replacement.created_at = current.created_at
    replacement.updated_at = utc_now()
    _validate_pack(replacement)
    atomic_write_json(
        task_pack_path(index_repository, task_pack_id),
        replacement.model_dump(mode="json"),
    )
    return replacement


def archive_task_pack(
    index_repository: Path,
    task_pack_id: str,
    repository_id: str,
    expected_revision: int,
) -> TaskPack:
    pack = load_task_pack(index_repository, task_pack_id)
    replacement = pack.model_copy(deep=True)
    replacement.lifecycle = "archived"
    return save_task_pack(
        index_repository,
        task_pack_id,
        repository_id,
        expected_revision,
        replacement,
    )


def list_task_packs(
    index_repository: Path,
    repository_id: str,
    *,
    include_archived: bool = False,
) -> list[TaskPackSummary]:
    summaries: list[TaskPackSummary] = []
    directory = task_packs_directory(index_repository)
    if not directory.exists():
        return []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = _raw_payload(path)
            schema_version = str(payload.get("schema_version", "0"))
            task_repository_id = str(payload.get("repository_id", ""))
            if task_repository_id != repository_id:
                continue
            lifecycle = str(payload.get("lifecycle", "draft"))
            if lifecycle == "archived" and not include_archived:
                continue
            items = payload.get("items", [])
            values = items if isinstance(items, list) else []
            summaries.append(
                TaskPackSummary(
                    schema_version=schema_version,
                    task_pack_id=str(payload.get("task_pack_id", path.stem)),
                    repository_id=task_repository_id,
                    revision=int(str(payload.get("revision", 1))),
                    lifecycle=lifecycle,
                    title=str(payload.get("title", "未命名任务")),
                    goal=str(payload.get("goal", "")),
                    item_count=len(values),
                    pending_count=sum(
                        isinstance(item, dict) and item.get("review_state") == "pending"
                        for item in values
                    ),
                    updated_at=str(payload.get("updated_at", "")),
                    writable=Version(schema_version) <= Version(TASK_PACK_SCHEMA_VERSION),
                )
            )
        except (OSError, ValueError, TaskPackError):
            continue
    return sorted(summaries, key=lambda item: item.updated_at, reverse=True)


def render_task_pack_markdown(pack: TaskPack) -> str:
    slot_by_id = {slot.slot_id: slot for slot in pack.slots}
    items_by_slot: dict[str, list[TaskPackItem]] = {slot.slot_id: [] for slot in pack.slots}
    for item in pack.items:
        items_by_slot.setdefault(item.slot_id, []).append(item)
    lines = [f"# {pack.title}", ""]
    if pack.goal:
        lines.extend([pack.goal, ""])
    lines.extend(
        [
            f"> Octopus 任务包 · {len(pack.items)} 项资料 · revision {pack.revision}",
            "",
        ]
    )
    for slot in sorted(pack.slots, key=lambda value: value.position):
        lines.extend([f"## {slot.name}", ""])
        if slot.description:
            lines.extend([slot.description, ""])
        items = sorted(items_by_slot.get(slot.slot_id, []), key=lambda value: value.position)
        if not items:
            lines.extend(["- 暂无资料。", ""])
            continue
        for item in items:
            status = "已确认" if item.review_state == "confirmed" else "待核验"
            label = item.raw_relative_path or item.name
            lines.append(f"- **{item.name}** · {status} · `{label}`")
            if item.rationale:
                lines.append(f"  - 加入原因：{item.rationale}")
            for anchor in item.anchors[:5]:
                excerpt = f" · {anchor.text_excerpt}" if anchor.text_excerpt else ""
                lines.append(f"  - `{anchor.locator}` · {anchor.kind}{excerpt}")
        lines.append("")
    if pack.excluded_node_ids:
        lines.extend(
            [
                "## 已排除候选",
                "",
                *[f"- `{node_id}`" for node_id in pack.excluded_node_ids],
                "",
            ]
        )
    unknown_slots = sorted(set(items_by_slot) - set(slot_by_id))
    if unknown_slots:
        raise TaskPackError("Task pack contains items in unknown slots")
    return "\n".join(lines).rstrip() + "\n"
