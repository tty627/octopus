from __future__ import annotations

import json
import os
import socket
from contextlib import suppress
from pathlib import Path
from typing import Any

from .models import utc_now


class RepositoryBusyError(RuntimeError):
    pass


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still means the PID exists.
        try:
            exit_code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
                exit_code.value == still_active
            )
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class RepositoryLock:
    def __init__(self, path: Path, operation: str, raw_repo_id: str, index_path: Path) -> None:
        self.path = path
        self.payload: dict[str, Any] = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": utc_now(),
            "operation": operation,
            "raw_repo_id": raw_repo_id,
            "index_repository_path": str(index_path),
        }
        self.acquired = False

    def __enter__(self) -> RepositoryLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing: dict[str, Any] = {}
            with suppress(OSError, json.JSONDecodeError):
                existing = json.loads(self.path.read_text(encoding="utf-8"))
            existing_pid = int(existing.get("pid", 0) or 0)
            if existing_pid and pid_is_alive(existing_pid):
                raise RepositoryBusyError(
                    f"Repository is locked by PID {existing_pid} "
                    f"({existing.get('operation', 'unknown')})"
                ) from None
            stale_path = self.path.with_name(f"{self.path.name}.stale-{existing_pid or 'unknown'}")
            try:
                os.replace(self.path, stale_path)
            except OSError as error:
                raise RepositoryBusyError(
                    f"Unable to recover stale repository lock: {error}"
                ) from error
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(self.payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        self.acquired = True
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self.acquired:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if int(current.get("pid", -1)) == os.getpid():
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
        self.acquired = False
