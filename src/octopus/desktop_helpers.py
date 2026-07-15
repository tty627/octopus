from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .models import SearchResult


def open_path(path: str | os.PathLike[str]) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Opening paths from the desktop app is supported only on Windows")
    startfile = cast(
        Callable[[str | os.PathLike[str]], None] | None,
        getattr(os, "startfile", None),
    )
    if startfile is None:
        raise RuntimeError("Windows path opener is unavailable")
    startfile(path)


def suggest_index_path(raw: Path) -> Path:
    base = raw.parent / f"{raw.name}-Octopus-Index"
    candidate = base
    number = 2
    while candidate.exists():
        candidate = raw.parent / f"{raw.name}-Octopus-Index-{number}"
        number += 1
    return candidate


def format_bytes(value: int) -> str:
    if value >= 1024**3:
        return f"{value / 1024**3:.1f} GiB"
    return f"{value / 1024**2:.1f} MiB"


def result_detail_text(result: SearchResult) -> str:
    evidence = "；".join(
        f"{item.locator}：{item.text_excerpt or item.kind}" for item in result.evidence[:3]
    )
    risks = "、".join(result.risk_flags) or "无已知风险"
    return (
        f"推荐原因：{result.explanation or '来自相关索引'}\n"
        f"证据定位：{evidence or '暂无内部定位'}\n"
        f"状态：{result.status}；风险：{risks}"
    )
