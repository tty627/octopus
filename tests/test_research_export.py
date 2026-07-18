from __future__ import annotations

import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import octopus.research_export as research_export
from octopus.citations import CitationRecord
from octopus.config import load_global_config, save_global_config
from octopus.models import GlobalWorkspace
from octopus.research_export import export_research_bundle
from octopus.utils import sha256_file
from octopus.workspace_sources import EvidenceLocator, SourceRef
from octopus.workspace_tasks_v2 import (
    WorkspaceTask,
    WorkspaceTaskItem,
    WorkspaceTaskSlot,
)
from octopus.workspace_v2 import WorkspaceStore


def _task(workspace_id: str) -> WorkspaceTask:
    primary = WorkspaceTaskSlot(
        slot_id="primary",
        name="核心文献",
        description="重点来源",
        position=0,
    )
    empty = WorkspaceTaskSlot(
        slot_id="empty",
        name="研究缺口",
        description="尚无资料",
        position=1,
    )
    return WorkspaceTask(
        task_id="task-12345678",
        workspace_id=workspace_id,
        title='Research: <2026>/"Preview"',
        goal="核验研究证据",
        slots=[primary, empty],
        items=[
            WorkspaceTaskItem(
                item_id="item-1",
                document_id="document-1",
                content_hash="hash-1",
                verified_content_hash="hash-1",
                name="Paper One",
                relative_path="paper-one.pdf",
                source_ref=SourceRef(
                    kind="archive_member",
                    workspace_path="bundle.zip",
                    virtual_path="bundle.zip!/papers/paper-one.pdf",
                    container_path="bundle.zip",
                    member_path="papers/paper-one.pdf",
                ),
                locator=EvidenceLocator(kind="page", page_number=3, label="第 3 页"),
                excerpt="可核验证据",
                rationale="核心结论",
                slot_id="primary",
                review_state="confirmed",
                source_status="resolved",
                freshness_status="current",
                citation=CitationRecord(
                    citation_id="paper-one",
                    citation_type="article",
                    title="Paper One",
                    authors=["Ada Lovelace"],
                    year="2026",
                    publication_title="Journal",
                ),
            ),
            WorkspaceTaskItem(
                item_id="item-2",
                document_id="document-2",
                content_hash="hash-2",
                name="Changed Notes",
                relative_path="notes.txt",
                page_number=7,
                slot_id="primary",
                review_state="pending",
                source_status="source_unconfirmed",
                freshness_status="changed",
                position=1,
            ),
            WorkspaceTaskItem(
                item_id="item-3",
                document_id="missing-document",
                content_hash="hash-3",
                name="Missing Source",
                relative_path="missing.docx",
                slot_id="primary",
                review_state="confirmed",
                source_status="resolved",
                freshness_status="missing",
                position=2,
            ),
        ],
    )


def test_research_markdown_and_safe_names_cover_locators_and_statuses() -> None:
    task = _task("workspace-1")
    markdown = research_export._render_markdown(task, "gb-t-7714-2015")
    assert markdown.startswith("# Research: <2026>/\"Preview\"")
    assert "bundle.zip!/papers/paper-one.pdf" in markdown
    assert "第 3 页" in markdown
    assert "Changed Notes** · 来源已变化，需重新核验 · 第 7 页" in markdown
    assert "## 研究缺口\n\n尚无资料\n\n暂无资料。" in markdown
    assert "引用：Ada Lovelace. Paper One[J]." in markdown
    assert research_export._safe_name('  <>:"/\\|?*  ') == "-"
    assert research_export._safe_name("." * 200, "fallback") == "fallback"
    assert research_export._item_source_label(task.items[1]) == "notes.txt"


def test_export_research_bundle_writes_manifest_citations_and_selected_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "workspace-1"
    raw = tmp_path / "raw"
    storage = tmp_path / "storage"
    raw.mkdir()
    storage.mkdir()
    source = raw / "paper-one.pdf"
    source.write_bytes(b"source bytes")
    nested_source = raw / "a" / "b.txt"
    nested_source.parent.mkdir()
    nested_source.write_bytes(b"nested source bytes")
    flat_source = raw / "a-b.txt"
    flat_source.write_bytes(b"flat source bytes")
    config = load_global_config()
    config.workspaces[workspace_id] = GlobalWorkspace(
        workspace_id=workspace_id,
        name="Research",
        raw_path=str(raw),
        storage_path=str(storage),
    )
    save_global_config(config)
    task = _task(workspace_id)
    task.items.extend(
        [
            WorkspaceTaskItem(
                item_id="item-4",
                document_id="document-4",
                content_hash=sha256_file(nested_source),
                verified_content_hash=sha256_file(nested_source),
                name="b.txt",
                relative_path="a/b.txt",
                source_ref=SourceRef(
                    kind="physical",
                    workspace_path="a/b.txt",
                    virtual_path="a/b.txt",
                ),
                slot_id="primary",
                review_state="confirmed",
                source_status="resolved",
                freshness_status="current",
            ),
            WorkspaceTaskItem(
                item_id="item-5",
                document_id="document-5",
                content_hash=sha256_file(flat_source),
                verified_content_hash=sha256_file(flat_source),
                name="a-b.txt",
                relative_path="a-b.txt",
                source_ref=SourceRef(
                    kind="physical",
                    workspace_path="a-b.txt",
                    virtual_path="a-b.txt",
                ),
                slot_id="primary",
                review_state="confirmed",
                source_status="resolved",
                freshness_status="current",
                position=4,
            ),
        ]
    )

    monkeypatch.setattr(research_export, "_reconfirm_task_sources", lambda value: value)

    def materialize(_store: WorkspaceStore, document_id: str) -> Path:
        sources = {
            "document-1": source,
            "document-4": nested_source,
            "document-5": flat_source,
        }
        if document_id in sources:
            return sources[document_id]
        raise FileNotFoundError(document_id)

    monkeypatch.setattr(WorkspaceStore, "materialize_document", materialize)
    output = export_research_bundle(task, citation_style="apa", include_sources=True)

    assert output.name.startswith("Research- -2026-Preview--task-123-")
    assert output.suffix == ".zip"
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert {
            "manifest.json",
            "references.bib",
            "references.txt",
            "research.md",
            "task.json",
        } <= names
        assert any(name.startswith("sources/") for name in names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["citation_style"] == "apa"
        assert manifest["include_sources"] is True
        assert manifest["items"][0]["included_source"] is True
        assert manifest["items"][1]["included_source"] is False
        assert manifest["items"][2]["included_source"] is False
        assert "source_error" in manifest["items"][2]
        nested_entry = manifest["items"][3]
        flat_entry = manifest["items"][4]
        assert nested_entry["export_path"] != flat_entry["export_path"]
        assert archive.read(nested_entry["export_path"]) == b"nested source bytes"
        assert archive.read(flat_entry["export_path"]) == b"flat source bytes"
        assert "@article{paperone" in archive.read("references.bib").decode("utf-8")
        assert "Lovelace, A. (2026)." in archive.read("references.txt").decode("utf-8")
        assert json.loads(archive.read("task.json"))["task_id"] == task.task_id

    without_sources = export_research_bundle(task, include_sources=False)
    with zipfile.ZipFile(without_sources) as archive:
        assert not any(name.startswith("sources/") for name in archive.namelist())


def test_concurrent_research_exports_use_isolated_output_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "workspace-concurrent-export"
    raw = tmp_path / "raw"
    storage = tmp_path / "storage"
    raw.mkdir()
    storage.mkdir()
    config = load_global_config()
    config.workspaces[workspace_id] = GlobalWorkspace(
        workspace_id=workspace_id,
        name="Concurrent export",
        raw_path=str(raw),
        storage_path=str(storage),
    )
    save_global_config(config)
    task = _task(workspace_id)
    barrier = threading.Barrier(2)
    monkeypatch.setattr(research_export, "_reconfirm_task_sources", lambda value: value)

    def create(style: str) -> Path:
        def synchronize(progress: dict[str, object]) -> None:
            if progress.get("phase") == "packaging":
                barrier.wait(timeout=5)

        return export_research_bundle(
            task,
            citation_style=style,  # type: ignore[arg-type]
            progress_callback=synchronize,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        apa = executor.submit(create, "apa")
        chinese = executor.submit(create, "gb-t-7714-2015")
        outputs = [apa.result(timeout=10), chinese.result(timeout=10)]

    assert outputs[0] != outputs[1]
    manifests = []
    for output in outputs:
        with zipfile.ZipFile(output) as archive:
            manifests.append(json.loads(archive.read("manifest.json")))
    assert {manifest["citation_style"] for manifest in manifests} == {
        "apa",
        "gb-t-7714-2015",
    }


def test_export_research_bundle_rejects_unknown_workspace() -> None:
    with pytest.raises(FileNotFoundError, match="Workspace not found"):
        export_research_bundle(_task("missing-workspace"))


def test_export_research_bundle_rejects_changed_physical_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = "workspace-changed-source"
    raw = tmp_path / "raw"
    storage = tmp_path / "storage"
    raw.mkdir()
    storage.mkdir()
    source = raw / "notes.txt"
    source.write_bytes(b"current bytes")
    config = load_global_config()
    config.workspaces[workspace_id] = GlobalWorkspace(
        workspace_id=workspace_id,
        name="Changed source",
        raw_path=str(raw),
        storage_path=str(storage),
    )
    save_global_config(config)
    task = WorkspaceTask(
        task_id="task-changed-source",
        workspace_id=workspace_id,
        title="Changed source",
        slots=[WorkspaceTaskSlot(slot_id="primary", name="Evidence")],
        items=[
            WorkspaceTaskItem(
                item_id="item-changed-source",
                document_id="document-changed-source",
                content_hash="0" * 64,
                verified_content_hash="0" * 64,
                name="notes.txt",
                relative_path="notes.txt",
                source_ref=SourceRef(
                    kind="physical",
                    workspace_path="notes.txt",
                    virtual_path="notes.txt",
                ),
                slot_id="primary",
                review_state="confirmed",
                source_status="resolved",
                freshness_status="current",
            )
        ],
    )
    monkeypatch.setattr(research_export, "_reconfirm_task_sources", lambda value: value)
    monkeypatch.setattr(
        WorkspaceStore,
        "materialize_document",
        lambda _store, _document_id: source,
    )

    output = export_research_bundle(task, include_sources=True)

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["items"][0]["included_source"] is False
        assert "content changed" in manifest["items"][0]["source_error"]
        assert not any(name.startswith("sources/") for name in archive.namelist())
