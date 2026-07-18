from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image, ImageOps

from .models import AIUsage, RepositoryConfig, RepositoryIdentity
from .providers import ProviderError, create_provider
from .workspace_v2 import WorkspaceStore

MAX_VISION_EDGE = 1_600
MAX_VISION_ENCODED_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class PreparedVisionPage:
    content: bytes
    media_type: str
    width: int
    height: int
    encoded_size_bytes: int

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.media_type};base64,{encoded}"


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def prepare_vision_page(
    store: WorkspaceStore,
    document_id: str,
    page_number: int,
) -> PreparedVisionPage:
    document = store.get_document(document_id)
    supported = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
    if document.extension not in supported:
        raise ValueError("Vision analysis is available only for PDF pages and images")
    if document.extension != ".pdf" and page_number != 1:
        raise ValueError("Image documents contain exactly one selectable page")
    source = store.preview_path(document_id, page_number, variant="base")
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((MAX_VISION_EDGE, MAX_VISION_EDGE), Image.Resampling.LANCZOS)
        working = image.copy()

    content = b""
    while True:
        for quality in (88, 78, 68, 58):
            content = _encode_jpeg(working, quality)
            if len(base64.b64encode(content)) <= MAX_VISION_ENCODED_BYTES:
                return PreparedVisionPage(
                    content=content,
                    media_type="image/jpeg",
                    width=working.width,
                    height=working.height,
                    encoded_size_bytes=len(base64.b64encode(content)),
                )
        if max(working.size) <= 640:
            break
        next_size = (max(1, int(working.width * 0.8)), max(1, int(working.height * 0.8)))
        working = working.resize(next_size, Image.Resampling.LANCZOS)
    raise ValueError("Selected page cannot be reduced below the 5 MB vision limit")


def _provider_config(store: WorkspaceStore) -> RepositoryConfig:
    workspace = store.workspace
    config = RepositoryConfig(
        repository=RepositoryIdentity(
            raw_repo_id=workspace.workspace_id,
            raw_repository_path=workspace.raw_path,
            index_repository_path=workspace.storage_path,
            repository_name=workspace.name,
        )
    )
    config.ai_policy = workspace.ai_policy.model_copy(deep=True)
    return config


def vision_preflight(
    store: WorkspaceStore,
    document_id: str,
    page_number: int,
) -> dict[str, Any]:
    prepared = prepare_vision_page(store, document_id, page_number)
    workspace = store.workspace
    capabilities = workspace.ai_policy.tested_capabilities
    can_send = bool(
        workspace.ai_policy.enabled
        and workspace.vision_enabled
        and capabilities.get("vision", False)
    )
    mode: Literal["vision", "ocr_fallback"] = "vision" if can_send else "ocr_fallback"
    if not workspace.vision_enabled:
        warning = "页面图像授权未开启，本次只使用本地 OCR 文本。"
    elif not capabilities.get("vision", False):
        warning = "当前模型未通过视觉能力测试，本次只使用本地 OCR 文本。"
    elif not workspace.ai_policy.enabled:
        warning = "辅助模型未启用，本次只使用本地 OCR 文本。"
    else:
        warning = ""
    pricing_configured = (
        workspace.ai_policy.input_cost_per_million is not None
        and workspace.ai_policy.output_cost_per_million is not None
    )
    return {
        "workspace_id": workspace.workspace_id,
        "document_id": document_id,
        "page_number": page_number,
        "model": workspace.ai_policy.model,
        "mode": mode,
        "image_size_bytes": prepared.encoded_size_bytes,
        "width": prepared.width,
        "height": prepared.height,
        "max_edge": MAX_VISION_EDGE,
        "pricing_configured": pricing_configured,
        "cost_estimate_status": "usage_based" if pricing_configured else "unknown",
        "requires_confirmation": can_send,
        "warning": warning,
    }


def analyze_selected_page(
    store: WorkspaceStore,
    document_id: str,
    page_number: int,
    prompt: str,
    *,
    confirm_image_send: bool,
) -> dict[str, Any]:
    preflight = vision_preflight(store, document_id, page_number)
    page_text = store.page_text(document_id, page_number)
    if preflight["mode"] != "vision":
        return _ocr_fallback(preflight, page_text, str(preflight["warning"]))
    if not confirm_image_send:
        raise ValueError("Image transmission requires explicit confirmation")

    prepared = prepare_vision_page(store, document_id, page_number)
    provider = create_provider(_provider_config(store), require_network=True)
    analyze_image = getattr(provider, "analyze_image", None)
    if not callable(analyze_image):
        return _ocr_fallback(
            preflight,
            page_text,
            "当前兼容端点不支持视觉输入，已回退到 OCR 文本。",
        )
    try:
        answer = str(analyze_image(prompt.strip(), prepared.data_url)).strip()
    except ProviderError as error:
        return _ocr_fallback(
            preflight,
            page_text,
            f"视觉请求不可用（{type(error).__name__}），已回退到 OCR 文本。",
            getattr(provider, "usage", None),
        )
    usage = getattr(provider, "usage", AIUsage())
    return {
        **preflight,
        "mode": "vision",
        "answer": answer,
        "warning": "",
        "usage": usage.model_dump(mode="json"),
        "cost_known": usage.estimated_cost is not None,
    }


def _ocr_fallback(
    preflight: dict[str, Any],
    page_text: str,
    warning: str,
    usage: AIUsage | None = None,
) -> dict[str, Any]:
    answer = page_text.strip()
    if not answer:
        answer = "当前页没有可用的 OCR 文本，请打开原文件人工核对。"
    return {
        **preflight,
        "mode": "ocr_fallback",
        "answer": answer[:4_000],
        "warning": warning,
        "usage": (usage or AIUsage()).model_dump(mode="json"),
        "cost_known": bool(usage and usage.estimated_cost is not None),
    }
