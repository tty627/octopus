from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from octopus.models import ExtractionEvidence, TaskPackItem
from octopus.task_packs import (
    TaskPackConflictError,
    TaskPackVersionError,
    archive_task_pack,
    create_task_pack,
    list_task_packs,
    load_task_pack,
    render_task_pack_markdown,
    save_task_pack,
    task_pack_path,
)
from octopus.utils import atomic_write_json


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_task_pack_crud_revision_archive_and_raw_immutability(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, config = repository
    source = raw / "source.txt"
    source.write_text("Task pack source", encoding="utf-8")
    before = _sha256(source)
    repository_id = config.repository.raw_repo_id

    pack = create_task_pack(index, repository_id, "Launch brief", "Prepare the review")
    assert [slot.name for slot in pack.slots] == ["核心资料", "补充资料", "待核验"]
    assert task_pack_path(index, pack.task_pack_id).is_file()

    replacement = pack.model_copy(deep=True)
    replacement.items.append(
        TaskPackItem(
            item_id=str(uuid.uuid4()),
            node_id="node-1",
            name="source.txt",
            index_type="text",
            raw_relative_path="source.txt",
            content_id="sha256:test",
            slot_id=pack.slots[0].slot_id,
            rationale="Primary decision record",
            anchors=[
                ExtractionEvidence(
                    locator="paragraph:1",
                    kind="text",
                    text_excerpt="Task pack source",
                )
            ],
        )
    )
    saved = save_task_pack(
        index,
        pack.task_pack_id,
        repository_id,
        pack.revision,
        replacement,
    )
    assert saved.revision == 2
    assert load_task_pack(index, pack.task_pack_id).items[0].node_id == "node-1"
    with pytest.raises(TaskPackConflictError):
        save_task_pack(index, pack.task_pack_id, repository_id, 1, replacement)

    markdown = render_task_pack_markdown(saved)
    assert markdown.startswith("# Launch brief\n")
    assert "## 核心资料" in markdown
    assert "Primary decision record" in markdown
    assert "paragraph:1" in markdown

    archived = archive_task_pack(
        index, pack.task_pack_id, repository_id, saved.revision
    )
    assert archived.lifecycle == "archived"
    assert list_task_packs(index, repository_id) == []
    summaries = list_task_packs(index, repository_id, include_archived=True)
    assert summaries[0].task_pack_id == pack.task_pack_id
    assert summaries[0].item_count == 1
    assert _sha256(source) == before


def test_newer_task_pack_schema_is_summary_read_only(
    repository: tuple[Path, Path, object],
) -> None:
    _, index, config = repository
    repository_id = config.repository.raw_repo_id
    task_pack_id = str(uuid.uuid4())
    atomic_write_json(
        task_pack_path(index, task_pack_id),
        {
            "schema_version": "2.0",
            "task_pack_id": task_pack_id,
            "repository_id": repository_id,
            "revision": 7,
            "lifecycle": "saved",
            "title": "Future task pack",
            "goal": "Read the summary without overwriting it",
            "items": [{"review_state": "pending"}],
            "updated_at": "2026-07-15T00:00:00+00:00",
        },
    )

    summary = list_task_packs(index, repository_id)[0]
    assert summary.title == "Future task pack"
    assert summary.revision == 7
    assert summary.writable is False
    with pytest.raises(TaskPackVersionError):
        load_task_pack(index, task_pack_id)
