from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from octopus.engine import UpdateEngine
from octopus.providers import HeuristicProvider
from octopus.search import SearchIndex, analyze_terms, results_markdown


def test_chinese_and_english_analyzer() -> None:
    terms = analyze_terms("项目需求 Python API")
    assert "项目" in terms
    assert "需求" in terms
    assert "python" in terms
    assert "api" in terms


def test_search_database_is_rebuildable(repository: tuple[Path, Path, object]) -> None:
    raw, index, _ = repository
    (raw / "需求说明.txt").write_text("Octopus 项目需求和 Python API 设计", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    search = SearchIndex(index)
    results = search.search("项目需求")
    assert results
    assert any("Test Repository" in item.name for item in results)
    markdown = results_markdown("项目需求", results)
    assert "file:///" in markdown
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
    expanded = search.full_search("Test Repository")
    assert len(expanded) > len(basic)
    assert any(item.index_type == "leaf" for item in expanded)
