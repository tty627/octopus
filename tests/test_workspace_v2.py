from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pypdf
import pypdfium2
import pytest

from octopus import workspace_v2
from octopus.models import AIConfig, GlobalWorkspace
from octopus.workspace_v2 import (
    ExtractedPage,
    ExtractedSource,
    WorkspaceEvidence,
    WorkspaceSearchResult,
    WorkspaceStore,
    _apply_assisted_order,
    _extract_pdf,
    _passage_chunks,
    assisted_rerank,
    create_workspace,
    readability_score,
    search_terms,
)


def _store(raw: Path) -> WorkspaceStore:
    return WorkspaceStore(create_workspace(raw, raw.name))


def test_readability_rejects_mixed_script_and_repeated_mojibake() -> None:
    readable = "第六章 微分方程 常微分方程的基本概念 一阶微分方程 高阶线性微分方程"
    mixed = "ඞཨЖЖበխကခសស �\x00 ཀཁඞЖበխကခស �"
    repeated = "锟斤拷锟斤拷锟斤拷锟斤拷锟斤拷"

    assert readability_score(readable) >= 0.72
    assert readability_score(mixed) < 0.45
    assert readability_score(repeated) < 0.45


def test_chinese_search_terms_include_bigrams_and_trigrams() -> None:
    terms = search_terms("微分方程")

    assert {"微分", "方程", "微分方", "分方程", "微分方程"} <= set(terms)


def test_passage_chunking_is_bounded_and_overlapping() -> None:
    text = "第一段。" * 900

    chunks = _passage_chunks(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1_600 for chunk in chunks)
    assert chunks[0][-80:] in chunks[1]


def _search_result(document_id: str, name: str, rank: int) -> WorkspaceSearchResult:
    return WorkspaceSearchResult(
        document_id=document_id,
        name=name,
        relative_path=name,
        extension=Path(name).suffix,
        content_hash=f"hash-{document_id}",
        size_bytes=10,
        modified_at="2026-07-15T00:00:00+00:00",
        page_count=1,
        readability="readable",
        readability_score=0.9,
        source_uri=f"file:///C:/{name}",
        best_evidence=WorkspaceEvidence(
            page_number=1,
            excerpt="trusted local evidence",
            reason="正文包含查询内容",
            quality_score=0.9,
        ),
        rank=rank,
    )


def test_assisted_order_filters_unknown_ids_and_pins_exact_filename() -> None:
    exact = _search_result("exact", "微分方程.pdf", 1)
    other = _search_result("other", "复习提纲.pdf", 2)

    ordered = _apply_assisted_order(
        [exact, other],
        ["other", "invented", "exact"],
        ["exact"],
    )

    assert [item.document_id for item in ordered] == ["exact", "other"]
    assert [item.rank for item in ordered] == [1, 2]


def test_assisted_search_sends_only_text_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def create(**kwargs: object) -> object:
        captured.update(kwargs)
        message = SimpleNamespace(
            content='{"ordered_document_ids":["other","invented","exact"],"answer":"本地候选摘要"}'
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    fake_openai = SimpleNamespace(OpenAI=lambda **kwargs: fake_client)
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setattr(
        workspace_v2,
        "resolve_ai_api_key",
        lambda workspace_id, provider: SimpleNamespace(api_key="secret", source="test"),
    )
    workspace = GlobalWorkspace(
        workspace_id="workspace",
        name="资料",
        raw_path="C:/Raw",
        storage_path="C:/Cache",
        ai_policy=AIConfig(enabled=True, model="test-model"),
    )
    exact = _search_result("exact", "微分方程.pdf", 1)
    other = _search_result("other", "复习提纲.pdf", 2)

    ordered, answer = assisted_rerank(workspace, "微分方程", [exact, other])

    assert [item.document_id for item in ordered] == ["exact", "other"]
    assert answer == "本地候选摘要"
    request_text = str(captured["messages"])
    assert "image_url" not in request_text
    assert "file:///" not in request_text
    assert "trusted local evidence" in request_text


def test_workspace_sync_search_quality_and_source_read_only(tmp_path: Path) -> None:
    raw = tmp_path / "高数"
    raw.mkdir()
    exact = raw / "微分方程coursenotes.txt"
    exact.write_text(
        "第六章 微分方程\n常微分方程的基本概念与一阶微分方程。",
        encoding="utf-8",
    )
    (raw / "级数.txt").write_text("数项级数与幂级数的收敛判别。", encoding="utf-8")
    (raw / "乱码说明.txt").write_text("锟斤拷" * 20, encoding="utf-8")
    before = exact.read_bytes()
    store = _store(raw)

    report = store.sync()
    results = store.search("微分方程").results
    low_quality = store.search("乱码说明").results[0]

    assert report["discovered"] == 3
    assert results[0].name == "微分方程coursenotes.txt"
    assert results[0].best_evidence.reason == "文件名包含查询内容"
    assert low_quality.readability == "low"
    assert "锟斤拷" not in low_quality.best_evidence.excerpt
    assert store.search("锟斤拷").results == []
    assert exact.read_bytes() == before


def test_duplicate_files_keep_distinct_ids_and_move_reuses_only_removed_source(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "资料"
    raw.mkdir()
    (raw / "a.txt").write_text("same useful evidence text " * 8, encoding="utf-8")
    (raw / "b.txt").write_text("same useful evidence text " * 8, encoding="utf-8")
    store = _store(raw)
    store.sync()
    original = {item.name: item.document_id for item in store.list_documents()}

    (raw / "a.txt").rename(raw / "c.txt")
    store.sync()
    moved = {item.name: item.document_id for item in store.list_documents()}

    assert original["a.txt"] != original["b.txt"]
    assert moved == {"b.txt": original["b.txt"], "c.txt": original["a.txt"]}


def test_sync_removes_deleted_documents(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    raw.mkdir()
    source = raw / "temporary.txt"
    source.write_text("temporary searchable content " * 5, encoding="utf-8")
    store = _store(raw)
    store.sync()
    source.unlink()

    result = store.sync()

    assert result["removed"] == 1
    assert store.list_documents() == []
    assert store.search("temporary").results == []


class _FakeTextPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text_range(self) -> str:
        return self.text

    def close(self) -> None:
        return None


class _FakeBitmap:
    def to_pil(self) -> object:
        return object()

    def close(self) -> None:
        return None


class _FakePdfPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_textpage(self) -> _FakeTextPage:
        return _FakeTextPage(self.text)

    def render(self, *, scale: float) -> _FakeBitmap:
        assert scale == 2.0
        return _FakeBitmap()

    def close(self) -> None:
        return None


class _FakePdfDocument:
    def __init__(self, path: str, text: str) -> None:
        del path
        self.page = _FakePdfPage(text)

    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: int) -> _FakePdfPage:
        assert index == 0
        return self.page

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("pdfium_text", "pypdf_text", "ocr_text", "expected_method"),
    [
        ("Readable PDFium text with enough meaningful content for selection.", "", "", "pdfium"),
        ("bad", "Readable PyPDF fallback text with enough meaningful content.", "", "pypdf"),
        ("bad", "also bad", "Readable OCR output with enough meaningful content.", "ocr"),
    ],
)
def test_pdf_extraction_candidate_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pdfium_text: str,
    pypdf_text: str,
    ocr_text: str,
    expected_method: str,
) -> None:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"not-read-by-fakes")

    monkeypatch.setattr(
        pypdfium2,
        "PdfDocument",
        lambda value: _FakePdfDocument(value, pdfium_text),
    )
    fake_page = SimpleNamespace(extract_text=lambda: pypdf_text)
    fake_reader = SimpleNamespace(pages=[fake_page], metadata={})
    monkeypatch.setattr(pypdf, "PdfReader", lambda *args, **kwargs: fake_reader)
    monkeypatch.setattr(workspace_v2, "_ocr_text", lambda image: ocr_text)

    extracted = _extract_pdf(path)

    assert extracted.pages[0].extraction_method == expected_method


def test_ocr_text_accepts_an_empty_rapidocr_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_v2,
        "_ocr_engine",
        lambda: lambda image: SimpleNamespace(txts=None),
    )

    assert workspace_v2._ocr_text(object()) == ""


def test_pdf_preview_is_cached_by_content_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    raw = tmp_path / "资料"
    raw.mkdir()
    pdf = raw / "evidence.pdf"
    Image.new("RGB", (120, 160), "white").save(pdf, "PDF")
    monkeypatch.setattr(
        workspace_v2,
        "extract_source",
        lambda path: ExtractedSource(
            title=path.stem,
            page_count=1,
            pages=[
                ExtractedPage(
                    page_number=1,
                    text="Preview evidence text with reliable extraction.",
                    extraction_method="pdfium",
                    quality_score=0.9,
                )
            ],
        ),
    )
    store = _store(raw)
    store.sync()
    document = store.list_documents()[0]

    first = store.preview_path(document.document_id, 1)
    second = store.preview_path(document.document_id, 1)

    assert first == second
    assert first.is_file()
    assert first.parent.name == document.content_hash
