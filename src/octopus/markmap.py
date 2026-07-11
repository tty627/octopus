from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def render_markmap(markdown_path: Path, html_path: Path) -> None:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise RuntimeError("npx was not found; install Node.js to render Markmap HTML")
    result = subprocess.run(
        [
            npx,
            "--yes",
            "markmap-cli",
            str(markdown_path),
            "--offline",
            "--no-open",
            "--output",
            str(html_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Markmap failed").strip()
        raise RuntimeError(message[:2000])
