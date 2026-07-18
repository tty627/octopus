from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from octopus.config import global_config_lock, load_global_config, save_global_config
from octopus.models import AIUsage
from octopus.vision import (
    MAX_VISION_EDGE,
    MAX_VISION_ENCODED_BYTES,
    analyze_selected_page,
    prepare_vision_page,
    vision_preflight,
)
from octopus.workspace_v2 import (
    ExtractedPage,
    ExtractedSource,
    WorkspaceStore,
    create_workspace,
    get_workspace,
)


def _image_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WorkspaceStore, str]:
    from octopus import workspace_v2

    raw = tmp_path / "raw"
    raw.mkdir()
    image_path = raw / "large.png"
    Image.new("RGB", (3_200, 2_400), "white").save(image_path)

    monkeypatch.setattr(
        workspace_v2,
        "extract_source",
        lambda path, **_: ExtractedSource(
            title=path.name,
            page_count=1,
            pages=[
                ExtractedPage(
                    page_number=None,
                    text="研究流程包括资料发现、证据核验和结论整理。",
                    extraction_method="image_ocr",
                    quality_score=0.8,
                )
            ],
        ),
    )
    workspace = create_workspace(raw, "Vision")
    store = WorkspaceStore(workspace)
    store.sync()
    document = store.list_documents()[0]
    return store, document.document_id


def test_selected_image_is_resized_and_encoded_under_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, document_id = _image_workspace(tmp_path, monkeypatch)

    prepared = prepare_vision_page(store, document_id, 1)

    assert max(prepared.width, prepared.height) == MAX_VISION_EDGE
    assert prepared.encoded_size_bytes <= MAX_VISION_ENCODED_BYTES
    assert prepared.data_url.startswith("data:image/jpeg;base64,")
    with pytest.raises(ValueError, match="exactly one"):
        prepare_vision_page(store, document_id, 2)


def test_vision_requires_explicit_confirmation_and_falls_back_to_page_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, document_id = _image_workspace(tmp_path, monkeypatch)

    local = vision_preflight(store, document_id, 1)
    assert local["mode"] == "ocr_fallback"
    fallback = analyze_selected_page(
        store,
        document_id,
        1,
        "分析页面",
        confirm_image_send=False,
    )
    assert fallback["mode"] == "ocr_fallback"
    assert "证据核验" in fallback["answer"]

    with global_config_lock():
        config = load_global_config()
        workspace = config.workspaces[store.workspace.workspace_id]
        workspace.vision_enabled = True
        workspace.ai_policy.enabled = True
        workspace.ai_policy.tested_capabilities = {
            "text": True,
            "structured_output": True,
            "vision": True,
            "file_upload": False,
        }
        save_global_config(config)
    enabled_store = WorkspaceStore(get_workspace(store.workspace.workspace_id))
    assert vision_preflight(enabled_store, document_id, 1)["requires_confirmation"] is True
    with pytest.raises(ValueError, match="explicit confirmation"):
        analyze_selected_page(
            enabled_store,
            document_id,
            1,
            "分析页面",
            confirm_image_send=False,
        )

    calls: list[tuple[str, str]] = []

    class FakeProvider:
        usage = AIUsage(calls=1, input_tokens=20, output_tokens=10, total_tokens=30)

        def analyze_image(self, prompt: str, data_url: str) -> str:
            calls.append((prompt, data_url))
            return "页面展示了三阶段研究流程。"

    monkeypatch.setattr("octopus.vision.create_provider", lambda *_, **__: FakeProvider())
    analysis = analyze_selected_page(
        enabled_store,
        document_id,
        1,
        "分析页面",
        confirm_image_send=True,
    )
    assert analysis["mode"] == "vision"
    assert analysis["answer"] == "页面展示了三阶段研究流程。"
    assert len(calls) == 1
    assert calls[0][1].startswith("data:image/jpeg;base64,")
    assert analysis["usage"]["calls"] == 1
