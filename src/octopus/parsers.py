from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from .models import ContentParser, ExtractedDocument
from .utils import truncate

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".xml",
    ".html",
    ".css",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".c",
    ".cpp",
    ".h",
    ".java",
    ".go",
    ".rs",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


def is_plain_text(path: Path) -> bool:
    if path.suffix.casefold() in TEXT_EXTENSIONS:
        return True
    if path.suffix.casefold() == ".csv":
        return path.stat().st_size <= 5 * 1024 * 1024
    return False


def _rapid_ocr_text(image: Any) -> tuple[str, list[str]]:
    try:
        from rapidocr import RapidOCR  # type: ignore[import-not-found]
    except ImportError:
        return "", ["ocr_unavailable"]
    try:
        result = RapidOCR()(image)
        texts: list[str] = []
        if hasattr(result, "txts"):
            texts = [str(item) for item in result.txts]
        elif isinstance(result, tuple) and result:
            rows = result[0] or []
            for row in rows:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    value = row[1]
                    texts.append(str(value[0] if isinstance(value, (list, tuple)) else value))
        elif isinstance(result, list):
            for row in result:
                if isinstance(row, (list, tuple)) and len(row) >= 2:
                    value = row[1]
                    texts.append(str(value[0] if isinstance(value, (list, tuple)) else value))
        return "\n".join(texts), [] if texts else ["ocr_returned_no_text"]
    except Exception as error:  # OCR engines raise backend-specific exceptions.
        return "", [f"ocr_failed:{type(error).__name__}"]


class TextParser:
    def can_handle(self, path: Path) -> bool:
        return is_plain_text(path)

    def extract(self, path: Path) -> ExtractedDocument:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        structure = [line.strip() for line in text.splitlines() if line.lstrip().startswith("#")][
            :100
        ]
        if path.suffix.casefold() == ".csv":
            try:
                rows = list(csv.reader(io.StringIO(text[:50_000])))
                width = max((len(row) for row in rows), default=0)
                structure.insert(0, f"CSV sample: {len(rows)} rows, {width} columns")
            except csv.Error:
                pass
        return ExtractedDocument(
            name=path.name,
            document_type=path.suffix.casefold().lstrip(".") or "text",
            text=truncate(text),
            structure=structure,
        )


class PDFParser:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.casefold() == ".pdf"

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            import pypdfium2 as pdfium  # type: ignore[import-untyped]
            from pypdf import PdfReader
        except ImportError:
            return ExtractedDocument(
                name=path.name,
                document_type="pdf",
                quality_flags=["pdf_parser_unavailable"],
                unsupported=True,
            )
        texts: list[str] = []
        structure: list[str] = []
        flags: list[str] = []
        ocr_pages = 0
        reader = PdfReader(str(path), strict=False)
        renderer = pdfium.PdfDocument(str(path))
        try:
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if len(text) < 40 and ocr_pages < 20:
                    render_page = renderer[page_number - 1]
                    bitmap = render_page.render(scale=1.5)
                    image = bitmap.to_pil()
                    ocr_text, ocr_flags = _rapid_ocr_text(image)
                    bitmap.close()
                    render_page.close()
                    if ocr_text:
                        text = ocr_text
                        ocr_pages += 1
                    flags.extend(ocr_flags)
                texts.append(f"[Page {page_number}]\n{text}")
                first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
                structure.append(f"P{page_number}: {first_line[:120] or '[no text]'}")
        finally:
            renderer.close()
        metadata: dict[str, Any] = {
            str(key).lstrip("/"): str(value)
            for key, value in (reader.metadata or {}).items()
            if value
        }
        metadata["page_count"] = len(reader.pages)
        metadata["ocr_page_count"] = ocr_pages
        return ExtractedDocument(
            name=path.name,
            document_type="pdf",
            text=truncate("\n\n".join(texts)),
            structure=structure,
            metadata=metadata,
            quality_flags=sorted(set(flags)),
        )


class DocxParser:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.casefold() == ".docx"

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            from docx import Document
        except ImportError:
            return ExtractedDocument(
                name=path.name,
                document_type="word",
                quality_flags=["docx_parser_unavailable"],
                unsupported=True,
            )
        document = Document(str(path))
        text: list[str] = []
        structure: list[str] = []
        for paragraph in document.paragraphs:
            value = paragraph.text.strip()
            if not value:
                continue
            text.append(value)
            if paragraph.style and paragraph.style.name.casefold().startswith("heading"):
                structure.append(f"{paragraph.style.name}: {value[:160]}")
        for table_index, table in enumerate(document.tables, start=1):
            rows = []
            for row in table.rows[:20]:
                rows.append(" | ".join(cell.text.strip() for cell in row.cells[:20]))
            text.append(f"[Table {table_index}]\n" + "\n".join(rows))
            structure.append(
                f"Table {table_index}: {len(table.rows)} rows x {len(table.columns)} columns"
            )
        return ExtractedDocument(
            name=path.name,
            document_type="word",
            text=truncate("\n\n".join(text)),
            structure=structure,
            metadata={
                "paragraph_count": len(document.paragraphs),
                "table_count": len(document.tables),
            },
        )


class XlsxParser:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.casefold() in {".xlsx", ".xlsm"}

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            import openpyxl  # type: ignore[import-untyped]
        except ImportError:
            return ExtractedDocument(
                name=path.name,
                document_type="excel",
                quality_flags=["xlsx_parser_unavailable"],
                unsupported=True,
            )
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
        structure: list[str] = []
        samples: list[str] = []
        for sheet in workbook.worksheets:
            structure.append(
                f"Sheet {sheet.title}: rows={sheet.max_row}, "
                f"columns={sheet.max_column}, state={sheet.sheet_state}"
            )
            sample_rows = []
            for row in sheet.iter_rows(min_row=1, max_row=min(sheet.max_row, 20), values_only=True):
                sample_rows.append(
                    " | ".join("" if value is None else str(value) for value in row[:20])
                )
            samples.append(f"[Sheet: {sheet.title}]\n" + "\n".join(sample_rows))
        workbook.close()
        return ExtractedDocument(
            name=path.name,
            document_type="excel",
            text=truncate("\n\n".join(samples)),
            structure=structure,
            metadata={"sheet_names": workbook.sheetnames},
        )


class PptxParser:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.casefold() == ".pptx"

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            from pptx import Presentation
        except ImportError:
            return ExtractedDocument(
                name=path.name,
                document_type="ppt",
                quality_flags=["pptx_parser_unavailable"],
                unsupported=True,
            )
        presentation = Presentation(str(path))
        texts: list[str] = []
        structure: list[str] = []
        for number, slide in enumerate(presentation.slides, start=1):
            slide_text = [
                shape.text.strip()
                for shape in slide.shapes
                if hasattr(shape, "text") and shape.text.strip()
            ]
            title = slide_text[0] if slide_text else "[no title]"
            structure.append(f"Slide {number}: {title[:160]}")
            texts.append(f"[Slide {number}]\n" + "\n".join(slide_text))
            try:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    texts.append(f"[Slide {number} notes]\n{notes}")
            except (AttributeError, KeyError):
                pass
        return ExtractedDocument(
            name=path.name,
            document_type="ppt",
            text=truncate("\n\n".join(texts)),
            structure=structure,
            metadata={"slide_count": len(presentation.slides)},
        )


class ImageParser:
    def can_handle(self, path: Path) -> bool:
        return path.suffix.casefold() in IMAGE_EXTENSIONS

    def extract(self, path: Path) -> ExtractedDocument:
        try:
            from PIL import Image
        except ImportError:
            return ExtractedDocument(
                name=path.name,
                document_type="image",
                quality_flags=["image_parser_unavailable"],
                unsupported=True,
            )
        with Image.open(path) as image:
            metadata = {
                "width": image.width,
                "height": image.height,
                "mode": image.mode,
                "format": image.format,
            }
            image.load()
            text, flags = _rapid_ocr_text(image)
        return ExtractedDocument(
            name=path.name,
            document_type="image",
            text=truncate(text),
            structure=[f"Image: {metadata['width']}x{metadata['height']} {metadata['mode']}"],
            metadata=metadata,
            quality_flags=flags,
        )


class UnsupportedParser:
    def can_handle(self, path: Path) -> bool:
        return True

    def extract(self, path: Path) -> ExtractedDocument:
        return ExtractedDocument(
            name=path.name,
            document_type=path.suffix.casefold().lstrip(".") or "unknown",
            metadata={"size_bytes": path.stat().st_size},
            quality_flags=["unsupported_content_parser"],
            unsupported=True,
        )


class ParserRegistry:
    def __init__(self, parsers: list[ContentParser] | None = None) -> None:
        self.parsers = parsers or [
            PDFParser(),
            DocxParser(),
            XlsxParser(),
            PptxParser(),
            ImageParser(),
            TextParser(),
            UnsupportedParser(),
        ]

    def parser_for(self, path: Path) -> ContentParser:
        return next(parser for parser in self.parsers if parser.can_handle(path))

    def extract(self, path: Path) -> ExtractedDocument:
        return self.parser_for(path).extract(path)
