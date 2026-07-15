from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
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


def _write_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 18 Tf\n72 720 Td\n({escaped}) Tj\nET\n".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(value)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(payload)


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


def test_search_rejects_a_query_that_normalizes_to_empty(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    (raw / "notes.txt").write_text("useful evidence", encoding="utf-8")
    store = _store(raw)
    store.sync()

    with pytest.raises(ValueError, match="searchable text"):
        store.search("???")


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
        indexing_state="indexed",
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


def test_filename_prefix_ranks_before_infix_and_body_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    (raw / "微分方程.txt").write_text("exact filename", encoding="utf-8")
    (raw / "微分方程coursenotes.pdf").write_bytes(b"metadata-only-pdf")
    (raw / "常微分方程复习提纲.txt").write_text("review outline", encoding="utf-8")
    (raw / "课程复习.txt").write_text(
        "第六章 微分方程\n常微分方程的基本概念与一阶微分方程。",
        encoding="utf-8",
    )
    original_extract = workspace_v2.extract_source

    def extract(path: Path) -> ExtractedSource:
        if path.suffix.casefold() == ".pdf":
            return ExtractedSource(
                title=path.stem,
                pages=[],
                page_count=0,
                status="metadata_only",
            )
        return original_extract(path)

    monkeypatch.setattr(workspace_v2, "extract_source", extract)
    store = _store(raw)
    store.sync()

    results = store.search("微分方程").results
    names = [result.name for result in results]
    metadata_only = next(result for result in results if result.name.endswith(".pdf"))

    assert names[:4] == [
        "微分方程.txt",
        "微分方程coursenotes.pdf",
        "常微分方程复习提纲.txt",
        "课程复习.txt",
    ]
    assert metadata_only.indexing_state == "metadata_only"
    assert metadata_only.model_dump(mode="json")["indexing_state"] == "metadata_only"


def test_filename_match_does_not_fabricate_a_body_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    pdf = raw / "微分方程coursenotes.pdf"
    pdf.write_bytes(b"placeholder")
    monkeypatch.setattr(
        workspace_v2,
        "extract_source",
        lambda path: ExtractedSource(
            title=path.stem,
            page_count=1,
            pages=[
                ExtractedPage(
                    page_number=1,
                    text="Unrelated project meeting notes.",
                    extraction_method="pdfium",
                    quality_score=0.9,
                )
            ],
        ),
    )
    store = _store(raw)
    store.sync()

    result = store.search("微分方程").results[0]

    assert result.name == pdf.name
    assert result.best_evidence.page_number is None
    assert result.best_evidence.reason == "文件名包含查询内容"
    assert all("正文" not in item.reason for item in result.additional_evidence)


def test_health_does_not_count_metadata_or_failures_as_low_quality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    (raw / "readable.txt").write_text("readable local evidence " * 8, encoding="utf-8")
    (raw / "metadata.xlsx").write_bytes(b"office metadata")
    (raw / "broken.pdf").write_bytes(b"broken pdf")
    original_extract = workspace_v2.extract_source

    def extract(path: Path) -> ExtractedSource:
        if path.suffix.casefold() == ".xlsx":
            return ExtractedSource(
                title=path.stem,
                pages=[],
                page_count=0,
                status="metadata_only",
            )
        if path.name == "broken.pdf":
            raise RuntimeError("cannot parse")
        return original_extract(path)

    monkeypatch.setattr(workspace_v2, "extract_source", extract)
    store = _store(raw)
    store.sync()

    health = store.health()
    assert health.document_count == 3
    assert health.readable_count == 1
    assert health.partial_count == 0
    assert health.low_quality_count == 0
    assert health.metadata_only_count == 1
    assert health.failed_count == 1


def test_create_workspace_is_idempotent_and_rejects_overlapping_paths(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "source"
    nested = raw / "nested"
    sibling = tmp_path / "sibling"
    nested.mkdir(parents=True)
    sibling.mkdir()

    workspace = create_workspace(raw, "Source")

    assert create_workspace(raw, "Renamed").workspace_id == workspace.workspace_id
    with pytest.raises(ValueError, match="overlaps existing workspace"):
        create_workspace(nested, "Nested")
    with pytest.raises(ValueError, match="overlaps existing workspace"):
        create_workspace(tmp_path, "Parent")
    assert create_workspace(sibling, "Sibling").workspace_id != workspace.workspace_id


def test_concurrent_workspace_creation_reuses_one_workspace(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    callers = 8
    barrier = Barrier(callers)

    def create(_: int) -> str:
        barrier.wait(timeout=5)
        return create_workspace(raw, "Concurrent").workspace_id

    with ThreadPoolExecutor(max_workers=callers) as executor:
        workspace_ids = list(executor.map(create, range(callers)))

    assert len(set(workspace_ids)) == 1


def test_workspace_sync_reports_file_progress(tmp_path: Path) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    (raw / "a.txt").write_text("alpha evidence " * 8, encoding="utf-8")
    (raw / "b.txt").write_text("beta evidence " * 8, encoding="utf-8")
    updates: list[dict[str, object]] = []

    report = _store(raw).sync(updates.append)

    assert updates[0]["phase"] == "discovering"
    assert any(update["current_file"] == "a.txt" for update in updates)
    assert any(update["current_file"] == "b.txt" for update in updates)
    assert updates[-1] == {
        "phase": "completed",
        "discovered": 2,
        "processed": 2,
        "current_file": "",
        "indexed": 2,
        "unchanged": 0,
        "failed": 0,
        "removed": 0,
    }
    assert report["discovered"] == 2


def test_reprocess_document_forces_extraction_and_reports_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    source = raw / "notes.txt"
    source.write_text("trusted evidence " * 8, encoding="utf-8")
    store = _store(raw)
    store.sync()
    document_id = store.list_documents()[0].document_id
    original_extract = workspace_v2.extract_source
    extracted_paths: list[Path] = []

    def extract(path: Path) -> ExtractedSource:
        extracted_paths.append(path)
        return original_extract(path)

    monkeypatch.setattr(workspace_v2, "extract_source", extract)
    updates: list[dict[str, object]] = []

    result = store.reprocess_document(document_id, updates.append)

    assert extracted_paths == [source]
    assert result["reprocessed_document_id"] == document_id
    assert result["indexed"] == 1
    assert result["unchanged"] == 0
    assert updates[-1]["phase"] == "completed"
    assert updates[-1]["processed"] == 1
    assert updates[-1]["indexed"] == 1
    assert updates[-1]["unchanged"] == 0


def test_sync_retries_unchanged_failed_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    source = raw / "notes.txt"
    source.write_text("trusted evidence " * 8, encoding="utf-8")
    store = _store(raw)
    original_extract = workspace_v2.extract_source
    attempts = 0

    def fail_once(path: Path) -> ExtractedSource:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary parser failure")
        return original_extract(path)

    monkeypatch.setattr(workspace_v2, "extract_source", fail_once)

    first = store.sync()
    second = store.sync()

    assert first["failed"] == 1
    assert second["indexed"] == 1
    assert second["unchanged"] == 0
    assert attempts == 2
    assert store.list_documents()[0].indexing_state == "indexed"


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
    def __init__(self, path: str, text: str | list[str]) -> None:
        del path
        texts = [text] if isinstance(text, str) else text
        self.pages = [_FakePdfPage(value) for value in texts]

    def __len__(self) -> int:
        return len(self.pages)

    def __getitem__(self, index: int) -> _FakePdfPage:
        return self.pages[index]

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


def test_workspace_sync_reports_pdf_page_and_ocr_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "source"
    raw.mkdir()
    path = raw / "long.pdf"
    path.write_bytes(b"not-read-by-fakes")
    readable = "Readable PDFium text with enough meaningful content for selection."

    monkeypatch.setattr(
        pypdfium2,
        "PdfDocument",
        lambda value: _FakePdfDocument(value, [readable, "bad"]),
    )
    fake_pages = [
        SimpleNamespace(extract_text=lambda: readable),
        SimpleNamespace(extract_text=lambda: "also bad"),
    ]
    fake_reader = SimpleNamespace(pages=fake_pages, metadata={})
    monkeypatch.setattr(pypdf, "PdfReader", lambda *args, **kwargs: fake_reader)
    monkeypatch.setattr(
        workspace_v2,
        "_ocr_text",
        lambda image: "Readable OCR output with enough meaningful content.",
    )
    updates: list[dict[str, object]] = []

    report = _store(raw).sync(updates.append)

    ocr_update = next(
        update for update in updates if update.get("extraction_stage") == "ocr"
    )
    assert ocr_update["current_file"] == "long.pdf"
    assert ocr_update["current_page"] == 2
    assert ocr_update["page_count"] == 2
    assert ocr_update["pages_completed"] == 1
    assert ocr_update["ocr_pages_completed"] == 0
    assert updates[-1]["extraction_stage"] == "page_complete"
    assert updates[-1]["current_page"] == 2
    assert updates[-1]["page_count"] == 2
    assert updates[-1]["pages_completed"] == 2
    assert updates[-1]["ocr_pages_completed"] == 1
    assert report["indexed"] == 1


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


def test_pdf_preview_can_highlight_text_layer_matches(tmp_path: Path) -> None:
    from PIL import Image, ImageChops

    raw = tmp_path / "source"
    raw.mkdir()
    pdf = raw / "evidence.pdf"
    _write_text_pdf(pdf, "Highlight target evidence")
    store = _store(raw)
    store.sync()
    document = store.list_documents()[0]

    plain = store.preview_path(document.document_id, 1)
    highlighted = store.preview_path(document.document_id, 1, "Highlight target")
    cached = store.preview_path(document.document_id, 1, "Highlight target")

    assert highlighted == cached
    assert highlighted != plain
    assert "Highlight" not in highlighted.name
    with Image.open(plain) as plain_image, Image.open(highlighted) as highlighted_image:
        difference = ImageChops.difference(
            plain_image.convert("RGB"),
            highlighted_image.convert("RGB"),
        )
        assert difference.getbbox() is not None
    with pytest.raises(ValueError, match="highlight is too long"):
        store.preview_path(document.document_id, 1, "x" * 201)
