from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

import pytest

import octopus.workspace_tasks_v2 as workspace_tasks_v2
from octopus.config import load_global_config, save_global_config
from octopus.models import ExtractionEvidence, TaskPackItem
from octopus.task_packs import create_task_pack, save_task_pack
from octopus.utils import sha256_file
from octopus.workspace_tasks_v2 import (
    WorkspaceTask,
    WorkspaceTaskConflictError,
    WorkspaceTaskItem,
    archive_task,
    create_task,
    list_tasks,
    load_task,
    migrate_legacy_tasks,
    render_task_markdown,
    revalidate_task_sources,
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


def _create_task_with_source(
    tmp_path: Path,
) -> tuple[Path, WorkspaceStore, WorkspaceTaskItem, WorkspaceTask]:
    raw = tmp_path / "资料"
    raw.mkdir()
    source = raw / "source.txt"
    source.write_text("Original evidence", encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    store = WorkspaceStore(workspace)
    store.sync()
    document = store.list_documents()[0]
    task = create_task(workspace.workspace_id, "来源复核")
    replacement = task.model_copy(deep=True)
    replacement.items.append(
        WorkspaceTaskItem(
            item_id=str(uuid.uuid4()),
            document_id=document.document_id,
            content_hash=document.content_hash,
            name=document.name,
            relative_path=document.relative_path,
            page_number=1,
            excerpt="Original evidence",
            slot_id=task.slots[0].slot_id,
        )
    )
    saved = save_task(workspace.workspace_id, task.task_id, task.revision, replacement)
    return source, store, saved.items[0], saved


def test_task_source_reconfirmation_refreshes_moved_document_metadata(tmp_path: Path) -> None:
    source, store, original_item, saved = _create_task_with_source(tmp_path)
    destination = source.parent / "章节" / "renamed.txt"
    destination.parent.mkdir()
    source.rename(destination)
    store.sync()

    loaded = load_task(saved.workspace_id, saved.task_id)
    listed = list_tasks(saved.workspace_id)[0]
    markdown = render_task_markdown(saved)

    assert len(loaded.items) == 1
    assert loaded.items[0].document_id == original_item.document_id
    assert loaded.items[0].content_hash == original_item.content_hash
    assert loaded.items[0].source_status == "resolved"
    assert loaded.items[0].review_state == "confirmed"
    assert loaded.items[0].name == "renamed.txt"
    assert loaded.items[0].relative_path == "章节/renamed.txt"
    assert listed.item_count == 1
    assert listed.unresolved_count == 0
    assert "章节/renamed.txt" in markdown
    assert "人工核验：已确认" in markdown


def test_task_source_reconfirmation_marks_changed_and_deleted_sources(
    tmp_path: Path,
) -> None:
    source, store, original_item, saved = _create_task_with_source(tmp_path)
    source.write_text("Changed evidence with different bytes", encoding="utf-8")
    store.sync()

    changed = load_task(saved.workspace_id, saved.task_id)
    assert len(changed.items) == 1
    assert changed.items[0].content_hash == original_item.content_hash
    assert changed.items[0].excerpt == "Original evidence"
    assert changed.items[0].source_status == "source_unconfirmed"
    assert changed.items[0].review_state == "pending"
    changed_summary = list_tasks(saved.workspace_id)[0]
    assert changed_summary.unresolved_count == 1
    assert changed_summary.pending_count == 1
    changed_markdown = render_task_markdown(saved)
    assert "来源待重新确认" in changed_markdown
    assert "人工核验：待核验" in changed_markdown

    source.unlink()
    store.sync()
    deleted = load_task(saved.workspace_id, saved.task_id)
    assert len(deleted.items) == 1
    assert deleted.items[0].document_id == original_item.document_id
    assert deleted.items[0].content_hash == original_item.content_hash
    assert deleted.items[0].excerpt == "Original evidence"
    assert deleted.items[0].source_status == "source_unconfirmed"
    assert deleted.items[0].review_state == "pending"


def test_explicit_source_revalidation_reads_raw_content_before_sync(tmp_path: Path) -> None:
    source, store, original_item, saved = _create_task_with_source(tmp_path)
    source.write_text("Changed evidence that has not been synchronized", encoding="utf-8")

    indexed = store.list_documents()[0]
    assert indexed.content_hash == original_item.content_hash
    assert load_task(saved.workspace_id, saved.task_id).items[0].freshness_status == "current"

    revalidated = revalidate_task_sources(
        saved.workspace_id,
        saved.task_id,
        saved.revision,
    )

    assert revalidated.revision == saved.revision + 1
    assert revalidated.items[0].content_hash == original_item.content_hash
    assert revalidated.items[0].verified_content_hash == original_item.verified_content_hash
    assert revalidated.items[0].source_status == "source_unconfirmed"
    assert revalidated.items[0].review_state == "pending"
    assert revalidated.items[0].freshness_status == "changed"


def test_task_markdown_exports_pending_human_review_state(tmp_path: Path) -> None:
    _, _, _, saved = _create_task_with_source(tmp_path)
    replacement = saved.model_copy(deep=True)
    replacement.items[0].review_state = "pending"
    pending = save_task(
        saved.workspace_id,
        saved.task_id,
        saved.revision,
        replacement,
    )

    markdown = render_task_markdown(pending)

    assert "已定位 · 人工核验：待核验" in markdown


@pytest.mark.parametrize("second_operation", ["save", "archive"])
def test_concurrent_task_writes_allow_only_one_revision_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second_operation: str,
) -> None:
    raw = tmp_path / "资料"
    raw.mkdir()
    workspace = create_workspace(raw, "资料")
    task = create_task(workspace.workspace_id, "并发任务")
    replacement = task.model_copy(deep=True)
    replacement.goal = "保存方写入"
    second_replacement = task.model_copy(deep=True)
    second_replacement.goal = "另一保存方写入"
    destination = task_path(workspace.workspace_id, task.task_id)
    real_atomic_write_json = workspace_tasks_v2.atomic_write_json
    first_write_started = Event()
    release_first_write = Event()
    second_write_started = Event()
    calls_lock = Lock()
    call_count = 0

    def delayed_atomic_write_json(path: Path, payload: object) -> None:
        nonlocal call_count
        if path == destination:
            with calls_lock:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_write_started.set()
                assert release_first_write.wait(timeout=5)
            else:
                second_write_started.set()
        real_atomic_write_json(path, payload)

    monkeypatch.setattr(workspace_tasks_v2, "atomic_write_json", delayed_atomic_write_json)
    futures: list[Future[WorkspaceTask]] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures.append(
            executor.submit(
                save_task,
                workspace.workspace_id,
                task.task_id,
                task.revision,
                replacement,
            )
        )
        assert first_write_started.wait(timeout=5)
        if second_operation == "save":
            futures.append(
                executor.submit(
                    save_task,
                    workspace.workspace_id,
                    task.task_id,
                    task.revision,
                    second_replacement,
                )
            )
        else:
            futures.append(
                executor.submit(
                    archive_task,
                    workspace.workspace_id,
                    task.task_id,
                    task.revision,
                )
            )
        assert not second_write_started.wait(timeout=0.2)
        release_first_write.set()

    outcomes: list[WorkspaceTask] = []
    conflicts = 0
    for future in futures:
        try:
            outcomes.append(future.result())
        except WorkspaceTaskConflictError:
            conflicts += 1

    assert len(outcomes) == 1
    assert conflicts == 1
    assert call_count == 1
    assert load_task(workspace.workspace_id, task.task_id).revision == 2


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


def test_v1_task_migrated_before_first_sync_resolves_after_sync(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    legacy = tmp_path / "资料-Octopus-Index"
    raw.mkdir()
    legacy.mkdir()
    source = raw / "chapter.txt"
    source.write_text("首次同步后的证据恢复", encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    config = load_global_config()
    config.workspaces[workspace.workspace_id].legacy_index_path = str(legacy)
    save_global_config(config)
    workspace = config.workspaces[workspace.workspace_id]

    legacy_pack = create_task_pack(legacy, workspace.workspace_id, "旧任务")
    replacement = legacy_pack.model_copy(deep=True)
    replacement.items.append(
        TaskPackItem(
            item_id=str(uuid.uuid4()),
            node_id="legacy-before-sync",
            name=source.name,
            index_type="text",
            raw_relative_path=source.name,
            content_id=f"sha256:{sha256_file(source)}",
            slot_id=legacy_pack.slots[0].slot_id,
        )
    )
    legacy_pack = save_task_pack(
        legacy,
        legacy_pack.task_pack_id,
        workspace.workspace_id,
        legacy_pack.revision,
        replacement,
    )

    assert migrate_legacy_tasks(workspace) == {
        "migrated": 1,
        "skipped": 0,
        "unresolved": 1,
    }
    before_sync = load_task(workspace.workspace_id, legacy_pack.task_pack_id)
    assert before_sync.items[0].document_id == ""
    assert before_sync.items[0].source_status == "source_unconfirmed"
    assert before_sync.items[0].review_state == "pending"

    WorkspaceStore(workspace).sync()
    after_sync = load_task(workspace.workspace_id, legacy_pack.task_pack_id)

    assert after_sync.items[0].document_id
    assert after_sync.items[0].content_hash == sha256_file(source)
    assert after_sync.items[0].relative_path == source.name
    assert after_sync.items[0].source_status == "resolved"
    assert after_sync.items[0].review_state == "pending"


def test_task_source_reconfirmation_uses_path_to_disambiguate_duplicate_hashes(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "资料"
    legacy = tmp_path / "资料-Octopus-Index"
    first = raw / "a" / "same.txt"
    second = raw / "b" / "same.txt"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    legacy.mkdir()
    first.write_text("相同内容", encoding="utf-8")
    second.write_text("相同内容", encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    config = load_global_config()
    config.workspaces[workspace.workspace_id].legacy_index_path = str(legacy)
    save_global_config(config)
    workspace = config.workspaces[workspace.workspace_id]

    legacy_pack = create_task_pack(legacy, workspace.workspace_id, "重复内容")
    replacement = legacy_pack.model_copy(deep=True)
    replacement.items.append(
        TaskPackItem(
            item_id=str(uuid.uuid4()),
            node_id="duplicate-hash",
            name=first.name,
            index_type="text",
            raw_relative_path="a/same.txt",
            content_id=f"sha256:{sha256_file(first)}",
            slot_id=legacy_pack.slots[0].slot_id,
        )
    )
    legacy_pack = save_task_pack(
        legacy,
        legacy_pack.task_pack_id,
        workspace.workspace_id,
        legacy_pack.revision,
        replacement,
    )

    migrate_legacy_tasks(workspace)
    WorkspaceStore(workspace).sync()
    loaded = load_task(workspace.workspace_id, legacy_pack.task_pack_id)

    assert loaded.items[0].document_id
    assert loaded.items[0].relative_path == "a/same.txt"
    assert loaded.items[0].source_status == "resolved"


def test_v1_task_migration_does_not_resolve_changed_content_by_path(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    legacy = tmp_path / "资料-Octopus-Index"
    raw.mkdir()
    legacy.mkdir()
    source = raw / "chapter.txt"
    source.write_text("当前内容", encoding="utf-8")
    workspace = create_workspace(raw, "资料")
    config = load_global_config()
    config.workspaces[workspace.workspace_id].legacy_index_path = str(legacy)
    save_global_config(config)
    workspace = config.workspaces[workspace.workspace_id]
    WorkspaceStore(workspace).sync()

    legacy_pack = create_task_pack(legacy, workspace.workspace_id, "旧内容")
    replacement = legacy_pack.model_copy(deep=True)
    replacement.items.append(
        TaskPackItem(
            item_id=str(uuid.uuid4()),
            node_id="changed-path",
            name=source.name,
            index_type="text",
            raw_relative_path=source.name,
            content_id="sha256:old-content-hash",
            slot_id=legacy_pack.slots[0].slot_id,
        )
    )
    legacy_pack = save_task_pack(
        legacy,
        legacy_pack.task_pack_id,
        workspace.workspace_id,
        legacy_pack.revision,
        replacement,
    )

    migrate_legacy_tasks(workspace)
    loaded = load_task(workspace.workspace_id, legacy_pack.task_pack_id)

    assert loaded.items[0].document_id == ""
    assert loaded.items[0].content_hash == "old-content-hash"
    assert loaded.items[0].source_status == "source_unconfirmed"
