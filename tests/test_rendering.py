from __future__ import annotations

import json
from pathlib import Path

from octopus.models import ExtractedDocument, Fingerprint, GeneratedSummary, NodeRecord, NodeState
from octopus.rendering import parse_machine_header, render_leaf, validate_index_text


def test_leaf_has_one_json_and_preserves_user_blocks(repository: tuple[Path, Path, object]) -> None:
    raw, _, config = repository
    source = raw / "资料.bin"
    source.write_bytes(b"octopus")
    node = NodeRecord(
        node_id="node-1",
        node_kind="raw_file",
        raw_relative_path=source.name,
        state=NodeState.indexing,
        fingerprint=Fingerprint(
            size_bytes=7,
            modified_at="2026-01-01T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
            content_hash="abc",
        ),
    )
    document = ExtractedDocument(
        name=source.name,
        document_type="bin",
        structure=["Binary attachment"],
        quality_flags=["unsupported_content_parser"],
        unsupported=True,
    )
    summary = GeneratedSummary(
        one_sentence_summary="测试附件。",
        description="用于验证渲染器。",
        tag_rough=["test"],
        topic_keywords=["Octopus"],
    )
    first = render_leaf(config, node, source, document, summary)
    validate_index_text(first, "leaf")
    header, body = parse_machine_header(first)
    assert header["schema"]["index_type"] == "leaf"
    assert body.startswith("#")
    customized = first.replace("用户可在这里写入重点；自动更新不会覆盖。", "必须逐字保留的用户内容")
    second = render_leaf(config, node, source, document, summary, customized)
    assert "必须逐字保留的用户内容" in second
    assert second.count('"index_type": "leaf"') == 1
    json.loads(second[: json.JSONDecoder().raw_decode(second)[1]])
