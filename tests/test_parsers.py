from __future__ import annotations

from pathlib import Path

from docx import Document
from openpyxl import Workbook
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfWriter

from octopus.parsers import ParserRegistry


def test_builtin_document_parsers(tmp_path: Path) -> None:
    docx_path = tmp_path / "report.docx"
    document = Document()
    document.add_heading("Octopus Report", 1)
    document.add_paragraph("A compact project summary.")
    document.add_table(rows=2, cols=2)
    document.save(docx_path)

    xlsx_path = tmp_path / "data.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Summary"
    worksheet.append(["Item", "Value"])
    worksheet.append(["Octopus", "=1+1"])
    workbook.save(xlsx_path)

    pptx_path = tmp_path / "slides.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    textbox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    textbox.text = "Octopus overview"
    presentation.save(pptx_path)

    pdf_path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with pdf_path.open("wb") as stream:
        writer.write(stream)

    image_path = tmp_path / "scan.png"
    Image.new("RGB", (16, 16), "white").save(image_path)

    registry = ParserRegistry()
    assert registry.extract(docx_path).document_type == "word"
    assert registry.extract(xlsx_path).metadata["sheet_names"] == ["Summary"]
    assert registry.extract(pptx_path).metadata["slide_count"] == 1
    assert registry.extract(pdf_path).metadata["page_count"] == 1
    assert registry.extract(image_path).metadata["width"] == 16


def test_unsupported_parser_emits_explicit_flag(tmp_path: Path) -> None:
    path = tmp_path / "audio.xyz"
    path.write_bytes(b"unknown")
    result = ParserRegistry().extract(path)
    assert result.unsupported
    assert result.quality_flags == ["unsupported_content_parser"]
