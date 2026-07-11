from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document
from PIL import Image

from octopus.config import load_repository_config, load_repository_state
from octopus.engine import UpdateEngine
from octopus.rendering import read_machine_header


def raw_snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_end_to_end_index_is_read_only_and_bottom_up(repository: tuple[Path, Path, object]) -> None:
    raw, index, _ = repository
    (raw / "notes").mkdir()
    (raw / "notes" / "说明.txt").write_text("Octopus 本地索引项目说明", encoding="utf-8")
    Image.new("RGB", (32, 16), "white").save(raw / "diagram.png")
    before = raw_snapshot(raw)
    stats = UpdateEngine(index).run(force_path="*")
    after = raw_snapshot(raw)
    assert before == after
    assert stats.leaf_updated == 1
    assert stats.foldernode_updated == 2
    assert (index / "diagram.png.url").exists()
    leaves = list(index.rglob("*叶子索引.md"))
    folders = list(index.rglob("*FolderNode索引总结.md"))
    assert len(leaves) == 1
    assert len(folders) == 2
    leaf_header, _ = read_machine_header(leaves[0])
    assert leaf_header["schema"]["index_type"] == "leaf"
    assert leaf_header["attachment_card_layer"]["source"]["raw_relative_path"] == "diagram.png"
    root_header, _ = read_machine_header(next(path for path in folders if path.parent == index))
    types = {item["node_type"] for item in root_header["children_summary_layer"]["direct_children"]}
    assert types == {"leaf", "foldernode"}
    state = load_repository_state(index, load_repository_config(index))
    assert state.repository.last_successful_update_at
    assert state.scan.scan_generation == 1


def test_user_focus_survives_leaf_update(repository: tuple[Path, Path, object]) -> None:
    raw, index, _ = repository
    image_path = raw / "scan.png"
    Image.new("RGB", (20, 20), "white").save(image_path)
    UpdateEngine(index).run(force_path="*")
    leaf = next(index.rglob("*叶子索引.md"))
    text = leaf.read_text(encoding="utf-8")
    leaf.write_text(
        text.replace("用户可在这里写入重点；自动更新不会覆盖。", "保留我写的重点"),
        encoding="utf-8",
    )
    Image.new("RGB", (21, 20), "black").save(image_path)
    UpdateEngine(index).run(force_path="*")
    assert "保留我写的重点" in leaf.read_text(encoding="utf-8")


def test_manually_edited_folder_guidance_survives_update(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    source = raw / "guide.txt"
    source.write_text("first project guide", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    folder = next(path for path in index.rglob("*FolderNode索引总结.md") if path.parent == index)
    original = folder.read_text(encoding="utf-8")
    customized = original.replace(
        "本节点只聚合直接下级 compact signals；目录树仅用于展示 Raw Repository 拓扑。",
        "用户手工设定的聚合边界。",
    )
    folder.write_text(customized, encoding="utf-8")
    source.write_text("second project guide", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    assert "用户手工设定的聚合边界。" in folder.read_text(encoding="utf-8")


def test_pending_status_is_synced_mechanically_to_existing_leaf(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    source = raw / "draft.docx"
    document = Document()
    document.add_paragraph("stable draft")
    document.save(source)
    UpdateEngine(index).run(force_path="*")
    leaf = next(index.rglob("*叶子索引.md"))
    (raw / "~$draft.docx").write_bytes(b"lock")
    UpdateEngine(index).run(force_path="*")
    header, _ = read_machine_header(leaf)
    assert header["update_control"]["index_status"] == "pending_edit"
