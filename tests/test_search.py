from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from docx import Document
from PIL import Image

from octopus.engine import UpdateEngine
from octopus.models import GeneratedSearchAnswer, SearchResult
from octopus.providers import HeuristicProvider
from octopus.search import (
    SQLITE_ID_BATCH_SIZE,
    SearchIndex,
    analyze_terms,
    results_markdown,
    search_report_markdown,
)


def test_chinese_and_english_analyzer() -> None:
    terms = analyze_terms("项目需求 Python API")
    assert "项目" in terms
    assert "需求" in terms
    assert "python" in terms
    assert "api" in terms


def test_search_database_is_rebuildable(repository: tuple[Path, Path, object]) -> None:
    raw, index, _ = repository
    source = raw / "需求说明.docx"
    document = Document()
    document.add_paragraph("Octopus 项目需求和 Python API 设计")
    document.save(source)
    UpdateEngine(index).run(force_path="*")
    UpdateEngine(index).run(force_path="*")
    search = SearchIndex(index)
    results = search.search("项目需求")
    assert results
    assert any("Test Repository" in item.name for item in results)
    assert results[0].match_evidence
    assert results[0].open_target_uri.startswith("file:")
    leaf_result = next(
        item for item in search.search("需求说明.docx") if item.index_type == "leaf"
    )
    assert leaf_result.source_relative_path == "需求说明.docx"
    markdown = results_markdown("项目需求", results)
    assert "file:///" in markdown
    assert "推荐原因" in markdown
    assert "命中证据" in markdown
    search.database.unlink()
    assert search.search("Python API")


def test_full_search_expands_folder_children_without_reading_raw(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, index, _ = repository
    Image.new("RGB", (12, 12), "white").save(raw / "diagram.png")
    UpdateEngine(index).run(force_path="*")
    search = SearchIndex(index)
    basic = search.search("Test Repository")
    monkeypatch.setattr(
        "octopus.search.create_provider", lambda config, require_network: HeuristicProvider()
    )
    report = search.full_search_report("Test Repository")
    expanded = report.results
    assert len(expanded) > len(basic)
    assert any(item.index_type == "leaf" for item in expanded)
    assert report.answer.recommended_node_ids
    assert report.citations
    assert all(
        citation.node_id in {item.node_id for item in report.results}
        for citation in report.citations
    )
    markdown = search_report_markdown(report)
    assert "AI 任务摘要" in markdown
    assert "推荐阅读顺序与索引链接" in markdown
    assert "风险提示" in markdown
    assert "可验证引用" in markdown


def test_folder_expansion_batches_fifty_thousand_child_ids(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, index, _ = repository
    search = SearchIndex(index)
    search.rebuild()
    folders = [
        SearchResult(
            node_id=f"folder-{number}",
            index_type="foldernode",
            index_path=str(index / f"folder-{number}.md"),
            name=f"folder-{number}",
            summary="",
            description="",
            status="clean",
        )
        for number in range(100)
    ]
    monkeypatch.setattr(search, "search", lambda query, limit=20: folders)

    def folder_header(path: Path) -> tuple[dict[str, object], str]:
        number = int(path.stem.rsplit("-", 1)[1])
        children = [{"child_id": f"child-{number}-{child}"} for child in range(500)]
        return {"children_summary_layer": {"direct_children": children}}, ""

    monkeypatch.setattr("octopus.search.read_machine_header", folder_header)
    assert SQLITE_ID_BATCH_SIZE == 1_000
    assert search._expanded_results("scale", 100) == folders


def test_field_weighting_prefers_exact_filename_and_cache_auto_migrates(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    Image.new("RGB", (12, 12), "white").save(raw / "alpha-project.png")
    Image.new("RGB", (12, 12), "white").save(raw / "other.png")
    UpdateEngine(index).run(force_path="*")
    search = SearchIndex(index)

    results = search.search("alpha-project.png")
    assert results[0].name == "alpha-project.png"
    assert "exact_name" in results[0].match_reasons
    assert any(item.field == "name" for item in results[0].match_evidence)
    with closing(sqlite3.connect(search.database)) as connection:
        connection.execute("DROP TABLE search_metadata")
        connection.commit()
    migrated = search.search("alpha-project.png")
    assert migrated[0].name == "alpha-project.png"


def test_full_search_rejects_provider_citations_outside_candidates(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, index, _ = repository
    Image.new("RGB", (12, 12), "white").save(raw / "evidence.png")
    UpdateEngine(index).run(force_path="*")

    class InvalidCitationProvider(HeuristicProvider):
        def compose_search(self, query: str, results: list[object]) -> GeneratedSearchAnswer:
            return GeneratedSearchAnswer(
                summary="grounded answer [S999]",
                recommended_node_ids=["invented-node"],
                cited_node_ids=["invented-node"],
            )

    monkeypatch.setattr(
        "octopus.search.create_provider",
        lambda config, require_network: InvalidCitationProvider(),
    )
    report = SearchIndex(index).full_search_report("Test Repository")
    allowed = {result.node_id for result in report.results}
    assert report.answer.recommended_node_ids
    assert set(report.answer.recommended_node_ids) <= allowed
    assert set(report.answer.cited_node_ids) <= allowed
    assert {citation.node_id for citation in report.citations} <= allowed
    assert "S999" not in report.answer.summary
    assert any("无效引用" in warning for warning in report.answer.warnings)
    assert "[S" in report.answer.summary
