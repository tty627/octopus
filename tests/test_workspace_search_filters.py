from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from octopus.workspace_tasks_v2 import WorkspaceTaskItem, create_task, save_task
from octopus.workspace_v2 import WorkspaceStore, create_workspace


def _store(raw: Path) -> WorkspaceStore:
    return WorkspaceStore(create_workspace(raw, raw.name))


def test_search_combines_sql_filters_with_fts_and_task_scope(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    (raw / "included").mkdir(parents=True)
    (raw / "excluded").mkdir()
    (raw / "included" / "alpha.txt").write_text(
        "shared searchable evidence " * 8,
        encoding="utf-8",
    )
    (raw / "excluded" / "beta.md").write_text(
        "shared searchable evidence " * 8,
        encoding="utf-8",
    )
    store = _store(raw)
    store.sync()
    documents = {item.name: item for item in store.list_documents()}
    alpha = documents["alpha.txt"]

    task = create_task(store.workspace.workspace_id, "过滤范围")
    task.items.append(
        WorkspaceTaskItem(
            item_id=str(uuid.uuid4()),
            document_id=alpha.document_id,
            content_hash=alpha.content_hash,
            name=alpha.name,
            relative_path=alpha.relative_path,
            source_ref=alpha.source_ref,
            excerpt="shared searchable evidence",
            slot_id=task.slots[0].slot_id,
            review_state="confirmed",
        )
    )
    task = save_task(task.workspace_id, task.task_id, task.revision, task)

    report = store.search(
        "shared searchable evidence",
        path_prefix="included/",
        extensions=[".txt"],
        readability=[alpha.readability],
        indexing_states=["indexed"],
        source_kinds=["physical"],
        modified_from=alpha.modified_at,
        modified_to=alpha.modified_at,
        task_id=task.task_id,
    )

    assert [item.document_id for item in report.results] == [alpha.document_id]
    with pytest.raises(ValueError, match="valid UUID"):
        store.search("shared", task_id="../../outside")


def test_large_metadata_search_uses_bounded_sql_candidate_set(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    (raw / "needle-report.txt").write_text("metadata only", encoding="utf-8")
    store = _store(raw)
    store.sync()

    with store._connect() as connection:
        template = dict(connection.execute("SELECT * FROM documents LIMIT 1").fetchone())
        columns = list(template)
        placeholders = ", ".join("?" for _ in columns)
        rows = []
        for index in range(10_001):
            payload = dict(template)
            payload["document_id"] = str(uuid.uuid4())
            payload["relative_path"] = f"bulk/document-{index:05d}.txt"
            payload["name"] = f"document-{index:05d}.txt"
            payload["title"] = f"Document {index:05d}"
            payload["content_hash"] = f"hash-{index:05d}"
            payload["source_ref_json"] = ""
            rows.append(tuple(payload[column] for column in columns))
        connection.executemany(
            f"INSERT INTO documents ({', '.join(columns)}) VALUES ({placeholders})",
            rows,
        )

    report = store.search("needle report")

    assert [item.name for item in report.results] == ["needle-report.txt"]
