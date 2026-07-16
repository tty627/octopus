from __future__ import annotations

import os
import zipfile
from pathlib import Path

from octopus.workspace_tasks_v2 import WorkspaceTaskItem, create_task, save_task
from octopus.workspace_v2 import WorkspaceStore, create_workspace


def _store(raw: Path) -> WorkspaceStore:
    return WorkspaceStore(create_workspace(raw, raw.name))


def test_change_log_records_lifecycle_and_affected_task(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    source = raw / "notes.txt"
    source.write_text("initial evidence " * 5, encoding="utf-8")
    store = _store(raw)

    first = store.sync()
    document = store.list_documents()[0]
    task = create_task(store.workspace.workspace_id, "变化追踪")
    task.items.append(
        WorkspaceTaskItem(
            item_id="item-1",
            document_id=document.document_id,
            content_hash=document.content_hash,
            name=document.name,
            relative_path=document.relative_path,
            slot_id=task.slots[0].slot_id,
            excerpt="initial evidence",
            review_state="confirmed",
        )
    )
    save_task(task.workspace_id, task.task_id, task.revision, task)
    assert first["indexed"] == 1

    source.write_text("modified evidence " * 5, encoding="utf-8")
    store.sync()
    source.rename(raw / "moved.txt")
    store.sync()
    (raw / "moved.txt").unlink()
    store.sync()

    changes = store.list_changes(include_acknowledged=True)
    kinds = [str(item["kind"]) for item in changes]
    assert "added" in kinds
    assert "modified" in kinds
    assert "moved" in kinds
    assert "deleted" in kinds
    moved = next(item for item in changes if item["kind"] == "moved")
    assert moved["previous_path"] == "notes.txt"
    assert moved["relative_path"] == "moved.txt"
    assert task.task_id in moved["affected_task_ids"]


def test_corrupt_zip_keeps_last_members_and_logs_warning(tmp_path: Path) -> None:
    raw = tmp_path / "archives"
    raw.mkdir()
    archive = raw / "papers.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("paper.txt", b"stable evidence")
    store = _store(raw)
    store.sync()
    assert [item.name for item in store.list_members(store.list_documents()[0].document_id)] == [
        "paper.txt"
    ]

    archive.write_bytes(b"not a zip")
    store.sync()

    members = store.list_documents()
    assert any(item.name == "paper.txt" and item.freshness_status == "stale" for item in members)
    warnings = store.list_changes(include_acknowledged=True)
    assert any(item["kind"] == "parser_warning" for item in warnings)


def test_sync_detects_same_size_content_change_with_preserved_mtime(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    source = raw / "notes.txt"
    source.write_bytes(b"alpha evidence " * 8)
    store = _store(raw)
    store.sync()
    original_stat = source.stat()
    original_hash = store.list_documents()[0].content_hash

    source.write_bytes(b"bravo evidence " * 8)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    result = store.sync()

    document = store.list_documents()[0]
    assert result["unchanged"] == 0
    assert document.content_hash != original_hash
    assert store.search("bravo evidence").results
    assert not store.search("alpha evidence").results
