from __future__ import annotations

import ipaddress
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import global_config_path, load_global_config
from .locking import pid_is_alive
from .models import utc_now
from .runtime import octopus_command
from .utils import atomic_write_json


def service_directory() -> Path:
    return global_config_path().parent


def service_token_path() -> Path:
    return service_directory() / "service-token"


def api_pid_path() -> Path:
    return service_directory() / "api.pid"


def api_log_path() -> Path:
    return service_directory() / "api.log"


def api_stop_path() -> Path:
    return service_directory() / "api.stop"


def ensure_service_token() -> str:
    path = service_token_path()
    if path.exists():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError(f"Invalid Octopus service token file: {path}")
        return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(48)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return ensure_service_token()
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(token + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    with suppress(OSError):
        path.chmod(0o600)
    return token


def validate_loopback_host(host: str) -> str:
    normalized = host.strip().casefold()
    if normalized == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as error:
        raise ValueError("Octopus API host must be a numeric loopback address") from error
    if not address.is_loopback:
        raise ValueError("Octopus API may bind only to a loopback address")
    return str(address)


def _read_pid_payload() -> dict[str, Any] | None:
    path = api_pid_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"running": False, "error": "invalid_pid_file"}
    if not isinstance(payload, dict):
        return {"running": False, "error": "invalid_pid_file"}
    return payload


def _health_available(host: str, port: int, timeout: float = 0.5) -> bool:
    url_host = f"[{host}]" if ":" in host else host
    try:
        with urllib.request.urlopen(  # noqa: S310 - loopback URL is validated.
            f"http://{url_host}:{port}/v1/health",
            timeout=timeout,
        ) as response:
            return int(response.status) == 200
    except (OSError, urllib.error.URLError):
        return False


def api_status() -> dict[str, Any]:
    payload = _read_pid_payload()
    if payload is None:
        return {"running": False}
    pid = int(str(payload.get("pid", 0) or 0))
    running = pid_is_alive(pid)
    payload["running"] = running
    if running:
        host = str(payload.get("host", "127.0.0.1"))
        port = int(str(payload.get("port", 8765)))
        payload["healthy"] = _health_available(host, port)
    return payload


def _port_is_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def start_api_process(host: str | None = None, port: int | None = None) -> dict[str, Any]:
    current = api_status()
    if current.get("running"):
        raise RuntimeError(f"Octopus API is already running with PID {current.get('pid')}")
    config = load_global_config().service
    bind_host = validate_loopback_host(host or config.host)
    bind_port = port or config.port
    if not _port_is_available(bind_host, bind_port):
        raise RuntimeError(f"Octopus API port is already in use: {bind_host}:{bind_port}")
    ensure_service_token()
    api_stop_path().unlink(missing_ok=True)
    directory = service_directory()
    directory.mkdir(parents=True, exist_ok=True)
    log_stream = api_log_path().open("a", encoding="utf-8")
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
    process = subprocess.Popen(
        octopus_command(
            "_api-run",
            "--host",
            bind_host,
            "--port",
            str(bind_port),
        ),
        stdin=subprocess.DEVNULL,
        stdout=log_stream,
        stderr=log_stream,
        close_fds=True,
        creationflags=creationflags,
    )
    log_stream.close()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Octopus API exited during startup; inspect {api_log_path()}")
        status_payload = api_status()
        if status_payload.get("running") and status_payload.get("healthy"):
            return status_payload
        time.sleep(0.1)
    with suppress(OSError):
        process.terminate()
    raise RuntimeError(f"Octopus API did not become healthy; inspect {api_log_path()}")


def stop_api_process() -> dict[str, Any]:
    current = api_status()
    if not current.get("running"):
        api_pid_path().unlink(missing_ok=True)
        return {"running": False, "message": "Octopus API was not running"}
    if current.get("mode") == "windows_service":
        raise RuntimeError("Octopus API is hosted by Windows SCM; use 'octopus service stop'")
    pid = int(str(current["pid"]))
    atomic_write_json(api_stop_path(), {"pid": pid, "requested_at": utc_now()})
    deadline = time.monotonic() + 15.0
    while pid_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    forced = False
    if pid_is_alive(pid):
        forced = True
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as error:
            raise RuntimeError(f"Unable to stop Octopus API PID {pid}: {error}") from error
    api_pid_path().unlink(missing_ok=True)
    api_stop_path().unlink(missing_ok=True)
    return {"running": False, "pid": pid, "forced": forced}


def run_api_server(host: str, port: int) -> None:
    import uvicorn

    from .api import create_app

    bind_host = validate_loopback_host(host)
    ensure_service_token()
    payload = {
        "pid": os.getpid(),
        "host": bind_host,
        "port": port,
        "started_at": utc_now(),
        "token_path": str(service_token_path()),
    }
    atomic_write_json(api_pid_path(), payload)
    monitor_finished: threading.Event | None = None
    monitor: threading.Thread | None = None
    try:
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host=bind_host,
                port=port,
                log_level="info",
                access_log=False,
            )
        )
        stop_monitor_event = threading.Event()
        monitor_finished = stop_monitor_event

        def monitor_stop_request() -> None:
            while not stop_monitor_event.wait(0.2):
                try:
                    request = json.loads(api_stop_path().read_text(encoding="utf-8"))
                except (FileNotFoundError, OSError, json.JSONDecodeError):
                    continue
                if int(str(request.get("pid", -1))) == os.getpid():
                    server.should_exit = True
                    return

        monitor = threading.Thread(
            target=monitor_stop_request,
            name="octopus-api-stop-monitor",
            daemon=True,
        )
        monitor.start()
        server.run()
    finally:
        if monitor_finished is not None:
            monitor_finished.set()
        if monitor is not None:
            monitor.join(timeout=1.0)
        api_stop_path().unlink(missing_ok=True)
        existing = _read_pid_payload() or {}
        if int(str(existing.get("pid", -1))) == os.getpid():
            api_pid_path().unlink(missing_ok=True)
