from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from octopus.config import load_global_config, save_global_config
from octopus.models import ExtractionEvidence, TaskPackItem
from octopus.task_packs import create_task_pack, save_task_pack
from octopus.utils import sha256_file
from octopus.workspace_tasks_v2 import (
    WorkspaceTaskConflictError,
    WorkspaceTaskItem,
    archive_task,
    create_task,
    list_tasks,
    load_task,
    migrate_legacy_tasks,
    render_task_markdown,
    save_task,
    task_path,
)
from octopus.workspace_v2 import WorkspaceStore, create_workspace


def test_v2_task_crud_revision_archive_and_markdown(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    raw.mkdir()
    source = raw / "source.txt"
    source.write_text("Task evidence text", encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    store = WorkspaceStore(workspace)
    store.sync()
    document = store.list_documents()[0]
    before = source.read_bytes()

    task = create_task(workspace.workspace_id, "证据复核", "核对关键结论")
    replacement = task.model_copy(deep=True)
    replacement.items.append(
        WorkspaceTaskItem(
            item_id=str(uuid.uuid4()),
            document_id=document.document_id,
            content_hash=document.content_hash,
            name=document.name,
            relative_path=document.relative_path,
            page_number=1,
            excerpt="Task evidence text",
            rationale="主要证据",
            slot_id=task.slots[0].slot_id,
        )
    )

    saved = save_task(
        workspace.workspace_id,
        task.task_id,
        task.revision,
        replacement,
    )

    assert saved.revision == 2
    assert load_task(workspace.workspace_id, task.task_id).items[0].document_id
    assert "第 1 页" in render_task_markdown(saved)
    assert "Task evidence text" in render_task_markdown(saved)
    with pytest.raises(WorkspaceTaskConflictError):
        save_task(workspace.workspace_id, task.task_id, 1, replacement)
    archived = archive_task(workspace.workspace_id, task.task_id, saved.revision)
    assert archived.lifecycle == "archived"
    assert list_tasks(workspace.workspace_id) == []
    assert list_tasks(workspace.workspace_id, include_archived=True)[0].item_count == 1
    assert source.read_bytes() == before


def test_v1_task_migration_is_idempotent_and_preserves_unresolved_items(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "资料"
    legacy = tmp_path / "资料-Octopus-Index"
    raw.mkdir()
    legacy.mkdir()
    source = raw / "chapter.txt"
    source.write_text("微分方程页面证据 " * 8, encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    config = load_global_config()
    config.workspaces[workspace.workspace_id].legacy_index_path = str(legacy)
    save_global_config(config)
    workspace = config.workspaces[workspace.workspace_id]
    WorkspaceStore(workspace).sync()

    legacy_pack = create_task_pack(
        legacy,
        workspace.workspace_id,
        "旧任务",
        "迁移时保留证据",
    )
    replacement = legacy_pack.model_copy(deep=True)
    replacement.items.extend(
        [
            TaskPackItem(
                item_id=str(uuid.uuid4()),
                node_id="legacy-node-1",
                name=source.name,
                index_type="text",
                raw_relative_path=source.name,
                content_id=f"sha256:{sha256_file(source)}",
                slot_id=legacy_pack.slots[0].slot_id,
                rationale="保留用户重点",
                anchors=[
                    ExtractionEvidence(
                        locator="第 3 页",
                        kind="page",
                        text_excerpt="微分方程页面证据",
                    )
                ],
            ),
            TaskPackItem(
                item_id=str(uuid.uuid4()),
                node_id="legacy-missing",
                name="missing.pdf",
                index_type="leaf",
                raw_relative_path="missing.pdf",
                content_id="sha256:missing",
                slot_id=legacy_pack.slots[0].slot_id,
                rationale="不能静默丢失",
            ),
        ]
    )
    legacy_pack = save_task_pack(
        legacy,
        legacy_pack.task_pack_id,
        workspace.workspace_id,
        legacy_pack.revision,
        replacement,
    )
    legacy_task_path = (
        legacy / ".octopus" / "task-packs" / f"{legacy_pack.task_pack_id}.json"
    )
    legacy_bytes = legacy_task_path.read_bytes()

    first = migrate_legacy_tasks(workspace)
    second = migrate_legacy_tasks(workspace)
    migrated = load_task(workspace.workspace_id, legacy_pack.task_pack_id)

    assert first == {"migrated": 1, "skipped": 0, "unresolved": 1}
    assert second == {"migrated": 0, "skipped": 1, "unresolved": 0}
    assert migrated.migrated_from_v1 is True
    assert migrated.items[0].page_number == 3
    assert migrated.items[0].excerpt == "微分方程页面证据"
    assert migrated.items[0].rationale == "保留用户重点"
    assert migrated.items[1].source_status == "source_unconfirmed"
    assert migrated.items[1].review_state == "pending"
    assert task_path(workspace.workspace_id, legacy_pack.task_pack_id).is_file()
    assert legacy_task_path.read_bytes() == legacy_bytes
