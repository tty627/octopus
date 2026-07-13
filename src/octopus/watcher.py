from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import load_repository_config, octopus_dir
from .engine import UpdateEngine
from .locking import pid_is_alive
from .models import utc_now
from .runtime import octopus_command
from .utils import atomic_write_json


def pid_path(index_repository: Path) -> Path:
    return octopus_dir(index_repository) / "watch.pid"


def watch_status(index_repository: Path) -> dict[str, object]:
    path = pid_path(index_repository)
    if not path.exists():
        return {"running": False}
    try:
        raw_payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "error": "invalid_pid_file"}
    if not isinstance(raw_payload, dict):
        return {"running": False, "error": "invalid_pid_file"}
    payload: dict[str, object] = dict(raw_payload)
    pid = int(str(payload.get("pid", 0) or 0))
    payload["running"] = pid_is_alive(pid)
    return payload


def start_watch(index_repository: Path) -> dict[str, object]:
    current = watch_status(index_repository)
    if current.get("running"):
        raise RuntimeError(f"Watcher is already running with PID {current.get('pid')}")
    directory = octopus_dir(index_repository)
    directory.mkdir(parents=True, exist_ok=True)
    log_stream = (directory / "watch.log").open("a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    process = subprocess.Popen(
        octopus_command("_watch-run", "--repository", str(index_repository)),
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=log_stream,
        close_fds=True,
        creationflags=creationflags,
    )
    log_stream.close()
    payload: dict[str, object] = {
        "pid": process.pid,
        "started_at": utc_now(),
        "index_repository_path": str(index_repository),
        "running": True,
    }
    atomic_write_json(pid_path(index_repository), payload)
    return payload


def stop_watch(index_repository: Path) -> dict[str, object]:
    status = watch_status(index_repository)
    if not status.get("running"):
        pid_path(index_repository).unlink(missing_ok=True)
        return {"running": False, "message": "Watcher was not running"}
    pid = int(str(status["pid"]))
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as error:
        raise RuntimeError(f"Unable to stop watcher PID {pid}: {error}") from error
    for _ in range(30):
        if not pid_is_alive(pid):
            break
        time.sleep(0.1)
    pid_path(index_repository).unlink(missing_ok=True)
    return {"running": False, "pid": pid}


def run_watch_loop(index_repository: Path) -> None:
    stopped = False

    def stop_handler(signum: int, frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop_handler)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, stop_handler)
    try:
        while not stopped:
            config = load_repository_config(index_repository)
            try:
                UpdateEngine(index_repository).run()
            except Exception as error:
                with (octopus_dir(index_repository) / "watch.log").open(
                    "a", encoding="utf-8", newline="\n"
                ) as stream:
                    stream.write(f"{utc_now()} update failed: {error}\n")
            seconds = max(1, config.watcher.scan_interval_minutes) * 60
            for _ in range(seconds):
                if stopped:
                    break
                time.sleep(1)
    finally:
        path = pid_path(index_repository)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if int(payload.get("pid", -1)) == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
