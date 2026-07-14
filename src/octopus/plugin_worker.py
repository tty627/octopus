from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path
from typing import Any

from .plugin_sdk import load_plugin_manifest


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def run_worker(plugin: Path, request: Path, response: Path) -> None:
    root, manifest = load_plugin_manifest(plugin)
    entrypoint = root.joinpath(*Path(manifest.entrypoint).parts).resolve()
    request = request.resolve()
    response = response.resolve()
    runtime_roots = {
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        Path(sys.exec_prefix).resolve(),
        Path(sys.base_exec_prefix).resolve(),
        Path(__file__).resolve().parent,
        root,
    }

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open" and args:
            raw_path = args[0]
            if isinstance(raw_path, int):
                return
            path = Path(os.fspath(raw_path)).resolve()
            mode = args[1] if len(args) > 1 else "r"
            writing = (
                isinstance(mode, str)
                and any(flag in mode for flag in ("w", "a", "x", "+"))
            )
            if writing:
                if path != response:
                    raise PermissionError("plugin filesystem write blocked")
                return
            if path == request or any(_inside(path, allowed) for allowed in runtime_roots):
                return
            raise PermissionError("plugin filesystem read blocked")
        if event.startswith("socket."):
            raise PermissionError("plugin network access blocked")
        if event in {"subprocess.Popen", "os.system", "ctypes.dlopen"}:
            raise PermissionError(f"plugin capability blocked: {event}")

    sys.addaudithook(audit)
    os.environ.clear()
    os.environ.update(
        {
            "OCTOPUS_PLUGIN_REQUEST": str(request),
            "OCTOPUS_PLUGIN_RESPONSE": str(response),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    sys.argv = [str(entrypoint)]
    runpy.run_path(str(entrypoint), run_name="__main__")


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--response", type=Path, required=True)
    options = parser.parse_args(arguments)
    run_worker(options.plugin, options.request, options.response)


if __name__ == "__main__":
    main()
