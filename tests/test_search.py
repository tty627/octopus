from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from docx import Document
from PIL import Image

from octopus.engine import UpdateEngine
from octopus.models import GeneratedSearchAnswer, SearchResult
from octopus.providers import (
    HeuristicProvider,
    ProviderAuthError,
    ProviderBudgetError,
    ProviderOutputError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransientError,
)
from octopus.search import (
    SEARCH_ALGORITHM_VERSION,
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
    assert leaf_result.raw_relative_path == "需求说明.docx"
    markdown = results_markdown("项目需求", results)
    assert "file:///" in markdown
    assert "推荐原因" in markdown
    assert "命中证据" in markdown
    search.database.unlink()
    assert search.search("Python API")


def test_plain_text_is_an_explainable_file_result(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    (raw / "budget-review.md").write_text(
        "# Budget review\nProject Atlas approval evidence", encoding="utf-8"
    )
    UpdateEngine(index).run(force_path="*")

    report = SearchIndex(index).search_report("Atlas budget approval", limit=5)

    result = next(item for item in report.results if item.name == "budget-review.md")
    assert result.index_type == "text"
    assert result.raw_relative_path == "budget-review.md"
    assert result.recommended_open_target == "source"
    assert result.evidence
    assert result.explanation
    assert report.search_algorithm_version == SEARCH_ALGORITHM_VERSION
    assert report.actual_mode == "local"


def test_auto_search_degrades_for_missing_key_and_provider_failure(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, index, _ = repository
    (raw / "fallback.txt").write_text("offline fallback evidence", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    missing_key = SearchIndex(index).search_report("fallback evidence", mode="auto")
    assert missing_key.actual_mode == "degraded"
    assert missing_key.degradation_reason in {"ai_disabled", "ai_key_not_configured"}
    assert missing_key.results

    class FailingProvider(HeuristicProvider):
        def rerank_search(
            self, query: str, results: list[SearchResult]
        ) -> list[SearchResult]:
            raise ProviderTransientError("offline")

    monkeypatch.setattr(
        "octopus.search.create_provider", lambda config, require_network: FailingProvider()
    )
    failed = SearchIndex(index).search_report("fallback evidence", mode="auto")
    assert failed.actual_mode == "degraded"
    assert failed.degradation_reason == "ai_unavailable"
    assert [item.node_id for item in failed.results] == [
        item.node_id for item in missing_key.results
    ]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderAuthError("auth"), "ai_auth_failed"),
        (ProviderQuotaError("quota"), "ai_quota_exhausted"),
        (ProviderBudgetError("budget"), "ai_budget_exhausted"),
        (ProviderRateLimitError("rate"), "ai_rate_limited"),
        (ProviderOutputError("json"), "ai_invalid_output"),
    ],
)
def test_auto_search_provider_error_codes_are_stable(
    repository: tuple[Path, Path, object],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    reason: str,
) -> None:
    raw, index, _ = repository
    (raw / "provider.txt").write_text("provider fallback", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")

    class FailingProvider(HeuristicProvider):
        def rerank_search(
            self, query: str, results: list[SearchResult]
        ) -> list[SearchResult]:
            raise error

    monkeypatch.setattr(
        "octopus.search.create_provider", lambda config, require_network: FailingProvider()
    )
    report = SearchIndex(index).search_report("provider fallback", mode="auto")
    assert report.actual_mode == "degraded"
    assert report.degradation_reason == reason
    assert report.results


def test_incremental_search_refresh_tracks_text_modify_move_and_delete(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    source = raw / "notes.txt"
    source.write_text("first edition", encoding="utf-8")
    first = UpdateEngine(index).run(force_path="*")
    assert first.search_refresh_mode == "rebuild"

    source.write_text("second edition migration", encoding="utf-8")
    modified = UpdateEngine(index).run(force_path="notes.txt")
    assert modified.search_refresh_mode == "incremental"
    assert modified.search_documents_upserted >= 1
    assert any(item.name == "notes.txt" for item in SearchIndex(index).search("migration"))

    moved = raw / "renamed-notes.txt"
    source.rename(moved)
    UpdateEngine(index).run(force_path="*")
    renamed = SearchIndex(index).search("renamed notes")
    assert any(item.name == "renamed-notes.txt" for item in renamed)
    assert not any(item.name == "notes.txt" for item in renamed)

    moved.unlink()
    UpdateEngine(index).run(force_path="*")
    assert not any(item.name == "renamed-notes.txt" for item in SearchIndex(index).search("notes"))


def test_failed_derived_refresh_is_rebuilt_from_manifest_generation(
    repository: tuple[Path, Path, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, index, _ = repository
    source = raw / "recovery.txt"
    source.write_text("before recovery", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    source.write_text("after recovery generation", encoding="utf-8")

    def fail_refresh(*args: object, **kwargs: object) -> dict[str, int | str]:
        raise sqlite3.OperationalError("injected refresh failure")

    monkeypatch.setattr(SearchIndex, "refresh", fail_refresh)
    stats = UpdateEngine(index).run(force_path="recovery.txt")
    assert stats.failed == 1

    results = SearchIndex(index).search("after recovery generation")
    assert any(item.name == "recovery.txt" for item in results)


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
    with closing(sqlite3.connect(search.database)) as connection:
        connection.execute(
            "UPDATE search_metadata SET value = '-1' WHERE key = 'manifest_generation'"
        )
        connection.commit()
    refreshed = search.search("alpha-project.png")
    assert refreshed[0].name == "alpha-project.png"


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
