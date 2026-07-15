from __future__ import annotations

import os
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory


def _write_blank_pdf(path: Path) -> None:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    document = pdfium.PdfDocument.new()
    try:
        page = document.new_page(400, 300)
        page.close()
        document.save(str(path))
    finally:
        document.close()


def run_v2_dependency_smoke() -> None:
    from PIL import Image

    from .credentials import read_stored_ai_api_key
    from .models import AIConfig, GlobalWorkspace
    from .workspace_v2 import WorkspaceStore, _ocr_engine

    # Calling the engine verifies that all three ONNX models and native runtime DLLs are usable.
    _ocr_engine()(Image.new("RGB", (128, 64), "white"))

    with TemporaryDirectory(prefix="Octopus-V2-Smoke-") as temporary:
        root = Path(temporary)
        raw = root / "raw"
        raw.mkdir()
        source = raw / "packaging-smoke.pdf"
        _write_blank_pdf(source)
        workspace = GlobalWorkspace(
            workspace_id="packaging-smoke",
            name="Packaging smoke",
            raw_path=str(raw),
            storage_path=str(root / "cache"),
            ai_policy=AIConfig(enabled=False),
        )
        store = WorkspaceStore(workspace)
        sync = store.sync()
        if sync["failed"] or sync["health"]["document_count"] != 1:
            raise RuntimeError("V2 workspace indexing smoke test failed")
        document = store.list_documents()[0]
        preview = store.preview_path(document.document_id, 1)
        if not preview.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("PDFium page preview smoke test failed")

    if os.name == "nt":
        import win32timezone  # noqa: F401

        credential_id = f"packaging-smoke-{uuid.uuid4()}"
        if read_stored_ai_api_key(credential_id):
            raise RuntimeError("Unexpected credential returned for packaging smoke target")
