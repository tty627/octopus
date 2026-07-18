from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path

from .citations import (
    DEFAULT_CITATION_STYLE,
    CitationRecord,
    CitationStyle,
    render_bibliography,
    render_bibtex,
    render_citation,
)
from .config import load_global_config
from .utils import atomic_write_text, sha256_file
from .workspace_tasks_v2 import WorkspaceTask, WorkspaceTaskItem, _reconfirm_task_sources
from .workspace_v2 import WorkspaceStore


def _safe_name(value: str, fallback: str = "资料包") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", value).strip(" .")
    return cleaned[:120] or fallback


def _fallback_citation(item: WorkspaceTaskItem) -> CitationRecord:
    return CitationRecord(
        citation_id=item.document_id,
        title=item.name,
        authors=[],
        carrier="Z",
        url=item.relative_path,
        confidence=0.0,
    )


def _item_source_label(item: WorkspaceTaskItem) -> str:
    if item.source_ref is not None and item.source_ref.virtual_path:
        return item.source_ref.virtual_path
    return item.relative_path or item.name


def _source_export_name(item: WorkspaceTaskItem) -> str:
    safe = _safe_name(_item_source_label(item), item.name)
    suffix = Path(safe).suffix[:20]
    stem = Path(safe).stem[:90] or "source"
    identity = f"{item.document_id}\0{item.item_id}\0{_item_source_label(item)}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}{suffix}"


def _item_review_label(item: WorkspaceTaskItem) -> str:
    if item.freshness_status in {"changed", "stale", "needs_review"}:
        return "来源已变化，需重新核验"
    if item.source_status != "resolved" or item.freshness_status in {"missing", "unavailable"}:
        return "来源不可访问"
    if (
        item.review_state == "confirmed"
        and item.verified_content_hash
        and item.verified_content_hash == item.content_hash
    ):
        return "已人工核验"
    return "未人工核验"


def _render_markdown(task: WorkspaceTask, style: CitationStyle) -> str:
    lines = [f"# {task.title}", ""]
    if task.goal:
        lines.extend([task.goal, ""])
    lines.extend(
        [
            f"> Octopus 研究资料包 · {len(task.items)} 条资料 · 引用格式：{style}",
            "",
        ]
    )
    by_slot: dict[str, list[WorkspaceTaskItem]] = {slot.slot_id: [] for slot in task.slots}
    for item in task.items:
        by_slot.setdefault(item.slot_id, []).append(item)
    citation_numbers: dict[str, int] = {}
    citations = [item.citation or _fallback_citation(item) for item in task.items]
    unique: list[CitationRecord] = []
    for citation in citations:
        identity = citation.citation_id or citation.doi or citation.title
        if identity not in citation_numbers:
            citation_numbers[identity] = len(unique) + 1
            unique.append(citation)
    for slot in sorted(task.slots, key=lambda value: value.position):
        lines.extend([f"## {slot.name}", "", slot.description, ""])
        items = sorted(by_slot.get(slot.slot_id, []), key=lambda value: value.position)
        if not items:
            lines.extend(["暂无资料。", ""])
            continue
        for item in items:
            citation = item.citation or _fallback_citation(item)
            identity = citation.citation_id or citation.doi or citation.title
            number = citation_numbers[identity]
            locator = item.locator.label if item.locator and item.locator.label else ""
            if not locator and item.page_number:
                locator = f"第 {item.page_number} 页"
            status = _item_review_label(item)
            location = f" · {locator}" if locator else ""
            lines.append(
                f"- **{item.name}** · {status}{location} · [{number}]({_item_source_label(item)})"
            )
            if item.excerpt:
                lines.append(f"  - 摘录：{item.excerpt}")
            if item.rationale:
                lines.append(f"  - 用途：{item.rationale}")
            lines.append(f"  - 引用：{render_citation(citation, style)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_research_bundle(
    task: WorkspaceTask,
    *,
    citation_style: CitationStyle = DEFAULT_CITATION_STYLE,
    include_sources: bool = False,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Path:
    """Create a local, deterministic research bundle without mutating raw sources."""
    style = citation_style
    workspace = load_global_config().workspaces.get(task.workspace_id)
    if workspace is None:
        raise FileNotFoundError("Workspace not found")
    task = _reconfirm_task_sources(task)
    if progress_callback is not None:
        progress_callback(
            {"phase": "verifying", "completed": 0, "total": len(task.items)}
        )
    store = WorkspaceStore(workspace)
    export_root = Path(workspace.storage_path).expanduser().resolve() / "exports"
    export_root.mkdir(parents=True, exist_ok=True)
    output = export_root / f"{_safe_name(task.title)}-{task.task_id[:8]}.zip"
    temporary_dir = Path(tempfile.mkdtemp(prefix="octopus-export-", dir=str(export_root)))
    try:
        markdown = _render_markdown(task, style)
        atomic_write_text(temporary_dir / "research.md", markdown)
        citations = [item.citation or _fallback_citation(item) for item in task.items]
        atomic_write_text(temporary_dir / "references.bib", render_bibtex(citations))
        atomic_write_text(
            temporary_dir / "references.txt",
            render_bibliography(citations, style) + ("\n" if citations else ""),
        )
        manifest: dict[str, object] = {
            "schema_version": "1.1",
            "task_id": task.task_id,
            "workspace_id": task.workspace_id,
            "title": task.title,
            "citation_style": style,
            "include_sources": include_sources,
            "items": [],
        }
        sources_root = temporary_dir / "sources"
        for item_number, item in enumerate(task.items, start=1):
            entry: dict[str, object] = {
                "item_id": item.item_id,
                "document_id": item.document_id,
                "name": item.name,
                "relative_path": item.relative_path,
                "source_ref": item.source_ref.model_dump(mode="json") if item.source_ref else None,
                "content_hash": item.content_hash,
                "verified_content_hash": item.verified_content_hash,
                "verified_at": item.verified_at,
                "review_state": item.review_state,
                "freshness_status": item.freshness_status,
                "source_status": item.source_status,
                "review_label": _item_review_label(item),
                "included_source": False,
            }
            if (
                include_sources
                and item.source_status == "resolved"
                and item.review_state == "confirmed"
            ):
                try:
                    source = store.materialize_document(item.document_id)
                    source_kind = item.source_ref.source_kind if item.source_ref else "physical"
                    if source_kind != "archive_member":
                        expected_hash = item.verified_content_hash or item.content_hash
                        if not expected_hash:
                            raise ValueError("Confirmed source has no verified content hash")
                        if sha256_file(source) != expected_hash:
                            raise ValueError("Confirmed source content changed after indexing")
                    source_name = _source_export_name(item)
                    destination = sources_root / source_name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                    if (
                        source_kind != "archive_member"
                        and sha256_file(destination) != expected_hash
                    ):
                        destination.unlink(missing_ok=True)
                        raise ValueError("Confirmed source changed while it was being exported")
                    entry["included_source"] = True
                    entry["export_path"] = destination.relative_to(temporary_dir).as_posix()
                except (OSError, ValueError, FileNotFoundError) as error:
                    entry["source_error"] = str(error)[:500]
                    entry["excluded_reason"] = "source_copy_failed"
            elif not include_sources:
                entry["excluded_reason"] = "source_copy_not_requested"
            elif item.source_status != "resolved":
                entry["excluded_reason"] = "source_unavailable"
            elif item.review_state != "confirmed":
                entry["excluded_reason"] = "not_human_verified"
            else:
                entry["excluded_reason"] = "source_not_current"
            cast_items = manifest["items"]
            assert isinstance(cast_items, list)
            cast_items.append(entry)
            if progress_callback is not None:
                progress_callback(
                    {
                        "phase": "collecting_sources",
                        "completed": item_number,
                        "total": len(task.items),
                        "current_file": item.name,
                    }
                )
        atomic_write_text(
            temporary_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        atomic_write_text(
            temporary_dir / "task.json",
            json.dumps(task.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        )
        temporary_output = output.with_suffix(".tmp.zip")
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "packaging",
                    "completed": len(task.items),
                    "total": len(task.items),
                }
            )
        with zipfile.ZipFile(temporary_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(temporary_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(temporary_dir).as_posix())
        temporary_output.replace(output)
        if progress_callback is not None:
            progress_callback(
                {
                    "phase": "completed",
                    "completed": len(task.items),
                    "total": len(task.items),
                }
            )
        return output
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)
