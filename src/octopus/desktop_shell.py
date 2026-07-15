from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from . import __version__
from .config import global_config_path
from .desktop_client import LocalApiClient
from .desktop_helpers import open_path, suggest_index_path
from .utils import atomic_write_json, atomic_write_text, load_json


def ui_directory() -> Path:
    return Path(__file__).resolve().parent / "ui_dist"


def ui_state_path() -> Path:
    return global_config_path().parent / "ui-state.json"


def _file_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Only local file URIs can be opened")
    value = unquote(parsed.path)
    if parsed.netloc:
        value = f"//{parsed.netloc}{value}"
    if os.name == "nt" and value.startswith("/") and len(value) > 2 and value[2] == ":":
        value = value[1:]
    return Path(value).resolve()


class DesktopBridge:
    def __init__(self, client: LocalApiClient) -> None:
        self._client = client
        self._window: Any = None

    def _attach(self, window: Any) -> None:
        self._window = window

    def bootstrap(self) -> dict[str, Any]:
        return {
            "base_url": self._client.base_url,
            "token": self._client.token,
            "product_version": __version__,
            "platform": sys.platform,
        }

    def choose_directory(self) -> str:
        if self._window is None:
            return ""
        import webview

        selected = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return str(selected[0]) if selected else ""

    def suggest_index_path(self, raw_path: str) -> str:
        return str(suggest_index_path(Path(raw_path).expanduser()))

    def save_text_file(self, suggested_name: str, content: str) -> dict[str, Any]:
        if self._window is None:
            return {"saved": False}
        if len(content.encode("utf-8")) > 5 * 1024 * 1024:
            raise ValueError("Text export exceeds the 5 MB desktop limit")
        import webview

        selected = self._window.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=Path(suggested_name).name,
        )
        if not selected:
            return {"saved": False}
        value = selected[0] if isinstance(selected, (list, tuple)) else selected
        path = Path(value).expanduser().resolve()
        atomic_write_text(path, content)
        return {"saved": True, "file": path.name}

    def open_uri(self, uri: str) -> dict[str, Any]:
        path = _file_uri_path(uri)
        if not path.exists():
            raise FileNotFoundError("The selected local source is unavailable")
        open_path(path)
        return {"opened": True, "name": path.name}

    def load_ui_state(self) -> dict[str, Any]:
        payload = load_json(ui_state_path(), {})
        return payload if isinstance(payload, dict) else {}

    def save_ui_state(self, state: dict[str, Any]) -> dict[str, bool]:
        allowed = {
            key: state[key]
            for key in ("page", "repository_id", "window", "task_pack_id")
            if key in state
        }
        atomic_write_json(ui_state_path(), allowed)
        return {"saved": True}


def smoke_test() -> int:
    index = ui_directory() / "index.html"
    if not index.is_file():
        print(f"Octopus UI assets are missing: {index}", file=sys.stderr)
        return 1
    if os.name == "nt":
        try:
            import webview  # noqa: F401
        except ImportError as error:
            print(f"pywebview is unavailable: {error}", file=sys.stderr)
            return 1
    print(f"Octopus {__version__} desktop UI smoke test passed")
    return 0


def _show_startup_error(message: str) -> None:
    if os.name == "nt":
        cast(Any, ctypes).windll.user32.MessageBoxW(0, message, "Octopus 无法启动", 0x10)
    else:
        print(message, file=sys.stderr)


def main() -> None:
    if "--smoke-test" in sys.argv:
        raise SystemExit(smoke_test())
    if os.name != "nt":
        raise SystemExit("Octopus desktop UI currently supports Windows 11 x64 only")
    try:
        import webview

        client = LocalApiClient.from_runtime(required_product_version=__version__)
        bridge = DesktopBridge(client)
        target = os.environ.get("OCTOPUS_UI_DEV_URL", f"{client.base_url}/ui/")
        window = webview.create_window(
            "Octopus",
            target,
            js_api=bridge,
            width=1440,
            height=900,
            min_size=(1100, 720),
            background_color="#f5f7f6",
        )
        bridge._attach(window)
        webview.start(gui="edgechromium", debug=bool(os.environ.get("OCTOPUS_UI_DEBUG")))
    except Exception as error:
        _show_startup_error(
            "Octopus 桌面端启动失败。请确认 Windows WebView2 Runtime 已安装，"
            f"然后重试。\n\n技术信息：{error}"
        )
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
