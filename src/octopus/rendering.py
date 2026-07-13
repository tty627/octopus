from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import __version__
from .models import (
    ExtractedDocument,
    FolderNodeHeader,
    GeneratedSummary,
    LeafHeader,
    NodeRecord,
    NodeState,
    RepositoryConfig,
    UpdateControl,
    utc_now,
)
from .utils import atomic_write_text, safe_slug, stable_text_hash

PROTECTED_PATTERNS = {
    "user_focus": re.compile(
        r"<!-- octopus:user:start:user_focus -->(.*?)<!-- octopus:user:end:user_focus -->",
        re.DOTALL,
    ),
    "automation_prompt": re.compile(
        r"<!-- octopus:user:start:automation_prompt -->"
        r"(.*?)<!-- octopus:user:end:automation_prompt -->",
        re.DOTALL,
    ),
}


def parse_machine_header(text: str) -> tuple[dict[str, Any], str]:
    source = text.lstrip("\ufeff\r\n\t ")
    value, end = json.JSONDecoder().raw_decode(source)
    if not isinstance(value, dict):
        raise ValueError("Octopus machine header must be a JSON object")
    return value, source[end:].lstrip("\r\n")


def read_machine_header(path: Path) -> tuple[dict[str, Any], str]:
    return parse_machine_header(path.read_text(encoding="utf-8-sig"))


def protected_blocks(old_text: str | None) -> dict[str, str]:
    if not old_text:
        return {}
    values: dict[str, str] = {}
    for name, pattern in PROTECTED_PATTERNS.items():
        match = pattern.search(old_text)
        if match:
            values[name] = match.group(1)
    return values


def _protected(name: str, values: dict[str, str], placeholder: str) -> str:
    content = values.get(name, f"\n{placeholder}\n")
    return f"<!-- octopus:user:start:{name} -->{content}<!-- octopus:user:end:{name} -->"


def _markdown_section(text: str, heading: str) -> str | None:
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def leaf_filename(source_name: str, node_id: str) -> str:
    base = f"{safe_slug(source_name)}的叶子索引.md"
    return base if len(base) <= 150 else f"{safe_slug(source_name, 100)}-{node_id[:8]}的叶子索引.md"


def foldernode_filename(folder_name: str, node_id: str) -> str:
    label = safe_slug(folder_name or "Raw Repository根目录")
    base = f"{label}文件夹的FolderNode索引总结.md"
    return base if len(base) <= 150 else f"{label[:100]}-{node_id[:8]}的FolderNode索引总结.md"


def collision_safe_path(path: Path, node_id: str) -> Path:
    if not path.exists():
        return path
    try:
        header, _ = read_machine_header(path)
        source = header.get("attachment_card_layer", {}).get("source", {})
        folder_source = header.get("folder_card_layer", {}).get("source", {})
        if source.get("source_id") == node_id or folder_source.get("folder_id") == node_id:
            return path
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return path.with_name(f"{path.stem}-{node_id[:8]}{path.suffix}")


def write_url_shortcut(path: Path, target: Path) -> None:
    target_uri = target.resolve().as_uri()
    atomic_write_text(path, f"[InternetShortcut]\nURL={target_uri}\n")


def render_leaf(
    config: RepositoryConfig,
    node: NodeRecord,
    source: Path,
    document: ExtractedDocument,
    summary: GeneratedSummary,
    old_text: str | None = None,
) -> str:
    stat = source.stat()
    values = protected_blocks(old_text)
    header = LeafHeader(
        summary_layer={
            "name": source.name,
            "one_sentence_summary": summary.one_sentence_summary,
            "description": summary.description,
            "document_type": document.document_type,
            "languages": [],
            "tag_rough": summary.tag_rough,
            "topic_keywords": summary.topic_keywords,
            "quality_flags": sorted(set(document.quality_flags)),
        },
        attachment_card_layer={
            "source": {
                "raw_repo_id": config.repository.raw_repo_id,
                "source_id": node.node_id,
                "content_id": node.fingerprint.content_hash,
                "raw_relative_path": node.raw_relative_path,
                "absolute_path_snapshot": str(source),
            },
            "metadata": {
                "file_uri": source.resolve().as_uri(),
                "filename": source.name,
                "extension": source.suffix,
                "size_bytes": stat.st_size,
                "created_at": node.fingerprint.created_at,
                "modified_at": node.fingerprint.modified_at,
                **document.metadata,
            },
            "attachments": [
                {
                    "attachment_id": node.node_id,
                    "role": "original",
                    "file_uri": source.resolve().as_uri(),
                    "raw_relative_path": node.raw_relative_path,
                    "filename": source.name,
                    "extension": source.suffix,
                    "size_bytes": stat.st_size,
                    "remarks": "",
                }
            ],
            "extraction_evidence": [
                item.model_dump(mode="json", exclude_none=True) for item in document.evidence
            ],
        },
        extraction_policy={
            "default_read_scope": "summary_layer_only",
            "summary_layer_required_for_folder_summary": True,
            "read_attachment_card_layer_when": [
                "user_requests_original_file",
                "agent_needs_file_location",
                "agent_needs_version_or_size_metadata",
                "agent_needs_markmap_or_export_link",
            ],
            "do_not_use_attachment_card_layer_for_initial_routing": True,
            "parser_version": document.parser_version,
            "text_characters": document.text_characters,
            "truncated": document.truncated,
            "extraction_stats": document.extraction_stats,
        },
        update_control=UpdateControl(
            index_status=NodeState.failed if document.unsupported else NodeState.indexed,
            last_seen_at=node.stability.last_seen_at,
            last_indexed_at=utc_now(),
            raw_fingerprint=node.fingerprint.content_hash or node.fingerprint.quick_hash,
            pending_reason="unsupported_format" if document.unsupported else "",
        ),
    )
    structure = "\n".join(f"- {item}" for item in document.structure) or "- 未提取到结构信息。"
    quality = "\n".join(f"- {item}" for item in document.quality_flags) or "- 未发现已知质量问题。"
    evidence = (
        "\n".join(
            f"- `{item.locator}` · {item.kind} · {item.text_excerpt or '仅结构定位'}"
            for item in document.evidence[:20]
        )
        or "- 未生成可引用的内部定位。"
    )
    reading = "\n".join(
        f"{index}. {item}" for index, item in enumerate(summary.recommended_reading, 1)
    )
    body = f"""# {source.name} 的叶子索引

## 摘要

{summary.description}

## 文件结构与内部索引

{structure}

## 推荐阅读位置

{reading or "1. 根据任务需要查看上述结构定位。"}

## 解析证据定位

{evidence}

## 提取质量

{quality}

### 用户重点标记区域

{_protected("user_focus", values, "用户可在这里写入重点；自动更新不会覆盖。")}

## 维护层

### 用户的自动化叶子索引建议与提示词

{_protected("automation_prompt", values, "用户可在这里写入下次更新提示词。")}

### 维护日志

- {utc_now()}: Octopus {__version__} 生成或更新索引。
"""
    return (
        json.dumps(
            header.model_dump(mode="json", exclude_none=True, by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + body
    )


def render_foldernode(
    config: RepositoryConfig,
    node: NodeRecord,
    source: Path,
    children: list[dict[str, Any]],
    summary: GeneratedSummary,
    tree_lines: list[str],
    old_text: str | None = None,
) -> str:
    values = protected_blocks(old_text)
    manually_edited = bool(
        old_text
        and node.indexing.section_hashes.get("generated_document")
        and node.indexing.section_hashes["generated_document"] != stable_text_hash(old_text)
    )
    file_count = sum(1 for item in children if item.get("node_type") in {"file", "leaf"})
    folder_count = sum(1 for item in children if item.get("node_type") == "foldernode")
    notable = [item for item in children if item.get("open_recommendation") == "high"][:10]
    folder_name = source.name if node.raw_relative_path else config.repository.repository_name
    header = FolderNodeHeader(
        summary_layer={
            "name": folder_name,
            "one_sentence_summary": summary.one_sentence_summary,
            "description": summary.description,
            "folder_type": "mixed_archive",
            "languages": [],
            "tag_rough": summary.tag_rough or ["文件夹索引"],
            "topic_keywords": summary.topic_keywords,
            "scope_boundary": f"仅覆盖 {node.raw_relative_path or '/'} 的直接下级节点。",
            "open_folder_recommendation": "high" if children else "low",
            "why_open_folder": "包含可继续展开的直接下级索引。" if children else "当前目录为空。",
            "recommended_entry_nodes": summary.recommended_reading,
            "quality_flags": sorted(
                {flag for item in children for flag in item.get("quality_flags", [])}
            ),
        },
        folder_card_layer={
            "source": {
                "raw_repo_id": config.repository.raw_repo_id,
                "folder_id": node.node_id,
                "content_snapshot_id": node.fingerprint.content_hash,
                "raw_relative_path": node.raw_relative_path,
                "absolute_path_snapshot": str(source),
            },
            "metadata": {
                "folder_uri": source.resolve().as_uri(),
                "folder_name": folder_name,
                "created_at": node.fingerprint.created_at,
                "modified_at": node.fingerprint.modified_at,
                "direct_file_count": file_count,
                "direct_folder_count": folder_count,
                "recursive_file_count": 0,
                "recursive_folder_count": 0,
                "total_size_bytes_estimate": sum(
                    int(item.get("size_bytes", 0)) for item in children
                ),
            },
            "links": {
                "raw_folder_link": source.resolve().as_uri(),
                "index_folder_link": "",
                "parent_foldernode": "",
                "child_foldernodes": [
                    item.get("index_link", "")
                    for item in children
                    if item.get("node_type") == "foldernode"
                ],
                "child_leaf_indexes": [
                    item.get("index_link", "")
                    for item in children
                    if item.get("node_type") == "leaf"
                ],
            },
        },
        children_summary_layer={
            "direct_children": children,
            "notable_children": notable,
            "text_files_without_leaf": [
                item for item in children if item.get("node_type") == "file"
            ],
            "non_text_files_with_leaf": [
                item for item in children if item.get("node_type") == "leaf"
            ],
            "subfolders_with_foldernode": [
                item for item in children if item.get("node_type") == "foldernode"
            ],
            "opaque_leaf_folders": [
                item for item in children if item.get("node_type") == "opaque_leaf_folder"
            ],
        },
        aggregation_policy={
            "generation_order": "bottom_up",
            "default_child_read_scope": "child_summary_layer_only",
            "consume_leaf_fields": ["summary_layer"],
            "consume_foldernode_fields": [
                "summary_layer",
                "children_summary_layer.notable_children",
            ],
            "text_file_handling": "summarize_directly_without_leaf_when_plain_text",
            "non_text_file_handling": "consume_existing_leaf_before_folder_summary",
            "do_not_copy_child_fulltext": True,
        },
        extraction_policy={
            "default_read_scope": "summary_layer_and_children_summary_layer",
            "read_folder_card_layer_when": [
                "user_requests_original_folder",
                "agent_needs_folder_location",
                "agent_needs_file_count_or_size_metadata",
                "agent_needs_to_open_or_export_links",
            ],
            "read_markdown_body_when": [
                "summary_layer_indicates_possible_relevance",
                "agent_needs_directory_tree",
                "agent_needs_recommended_reading_path",
                "agent_needs_child_node_rationale",
            ],
            "do_not_use_folder_card_layer_for_initial_routing": True,
        },
        update_control=UpdateControl(
            index_status=NodeState.indexed,
            last_seen_at=node.stability.last_seen_at,
            last_mechanical_update_at=utc_now(),
            last_ai_summary_update_at=utc_now(),
            content_snapshot_id=node.fingerprint.content_hash,
            pending_child_count=sum(
                1
                for item in children
                if item.get("index_status") in {"pending_edit", "pending_stable"}
            ),
            failed_child_count=sum(1 for item in children if item.get("index_status") == "failed"),
        ),
    )
    table_lines = [
        "| 下级节点 | 类型 | 一句话摘要 | 建议动作 | 索引状态 | 质量提示 |",
        "|---|---|---|---|---|---|",
    ]
    for child in children:
        table_lines.append(
            "| {name} | {node_type} | {summary} | {action} | {status} | {flags} |".format(
                name=str(child.get("name", "")).replace("|", "\\|"),
                node_type=child.get("node_type", ""),
                summary=str(child.get("one_sentence_summary", "")).replace("|", "\\|"),
                action=child.get("open_recommendation", ""),
                status=child.get("index_status", ""),
                flags=", ".join(child.get("quality_flags", [])),
            )
        )
    reading = "\n".join(
        f"{index}. {item}" for index, item in enumerate(summary.recommended_reading, 1)
    )
    if manually_edited and old_text:
        reading = _markdown_section(old_text, "推荐阅读路径") or reading
    boundary = "本节点只聚合直接下级 compact signals；目录树仅用于展示 Raw Repository 拓扑。"
    if manually_edited and old_text:
        boundary = _markdown_section(old_text, "聚合判断与边界") or boundary
    body = f"""# {folder_name} 文件夹的 FolderNode 索引总结

## 文件夹摘要

{summary.description}

## 目录树拓扑

```text
{chr(10).join(tree_lines) if tree_lines else folder_name + "/"}
```

## 下级节点摘要表

{chr(10).join(table_lines)}

## 推荐阅读路径

{reading or "1. 当前没有推荐下级节点。"}

## 聚合判断与边界

{boundary}

## 质量评估

- pending 子节点：{header.update_control.pending_child_count}
- failed 子节点：{header.update_control.failed_child_count}

### 用户重点标记区域

{_protected("user_focus", values, "用户可在这里写入重点；自动更新不会覆盖。")}

## 维护层

### 用户的自动化文件夹节点建议与提示词

{_protected("automation_prompt", values, "用户可在这里写入下次更新提示词。")}

### 维护日志

- {utc_now()}: Octopus {__version__} 生成或更新索引。
"""
    return (
        json.dumps(
            header.model_dump(mode="json", exclude_none=True, by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n\n"
        + body
    )


def validate_index_text(text: str, expected_type: str) -> None:
    header, body = parse_machine_header(text)
    if header.get("schema", {}).get("index_type") != expected_type:
        raise ValueError(f"Expected index_type={expected_type}")
    if not body.lstrip().startswith("#"):
        raise ValueError("Markdown body is missing a top-level heading")
    for name, pattern in PROTECTED_PATTERNS.items():
        if len(pattern.findall(text)) != 1:
            raise ValueError(f"Protected region {name} is missing or duplicated")
