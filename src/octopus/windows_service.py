from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .config import load_global_config
from .models import utc_now
from .service_control import (
    api_pid_path,
    ensure_service_token,
    service_token_path,
    validate_loopback_host,
)
from .utils import atomic_write_json

SERVICE_NAME = "OctopusIndexService"
SERVICE_DISPLAY_NAME = "Octopus Index Local Service"


if sys.platform == "win32":
    import servicemanager
    import win32service
    import win32serviceutil

    class OctopusWindowsService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_ = "Local-only Octopus indexing API and multi-repository scheduler"

        def __init__(self, args: list[str]) -> None:
            super().__init__(args)
            self.server: Any = None

        def SvcStop(self) -> None:  # noqa: N802 - Windows SCM callback name.
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self.server is not None:
                self.server.should_exit = True

        def SvcDoRun(self) -> None:  # noqa: N802 - Windows SCM callback name.
            import uvicorn

            from .api import create_app

            config = load_global_config().service
            host = validate_loopback_host(config.host)
            ensure_service_token()
            atomic_write_json(
                api_pid_path(),
                {
                    "pid": os.getpid(),
                    "host": host,
                    "port": config.port,
                    "started_at": utc_now(),
                    "token_path": str(service_token_path()),
                    "mode": "windows_service",
                },
            )
            servicemanager.LogInfoMsg(f"{SERVICE_NAME} starting")
            try:
                self.server = uvicorn.Server(
                    uvicorn.Config(
                        create_app(),
                        host=host,
                        port=config.port,
                        access_log=False,
                        log_level="info",
                    )
                )
                self.server.run()
            finally:
                api_pid_path().unlink(missing_ok=True)
                servicemanager.LogInfoMsg(f"{SERVICE_NAME} stopped")

else:

    class OctopusWindowsService:
        pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows SCM service control is available only on Windows")


def install_service(username: str | None = None, password: str | None = None) -> None:
    _require_windows()
    import win32service
    import win32serviceutil

    win32serviceutil.InstallService(
        "octopus.windows_service.OctopusWindowsService",
        SERVICE_NAME,
        SERVICE_DISPLAY_NAME,
        startType=win32service.SERVICE_AUTO_START,
        userName=username,
        password=password,
        description="Local-only Octopus indexing API and multi-repository scheduler",
        delayedstart=True,
    )


def start_service() -> None:
    _require_windows()
    import win32serviceutil

    win32serviceutil.StartService(SERVICE_NAME)


def stop_service() -> None:
    _require_windows()
    import win32serviceutil

    win32serviceutil.StopService(SERVICE_NAME)


def remove_service() -> None:
    _require_windows()
    import win32serviceutil

    win32serviceutil.RemoveService(SERVICE_NAME)


def service_status() -> dict[str, Any]:
    _require_windows()
    import pywintypes
    import win32service
    import win32serviceutil

    try:
        raw = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
    except pywintypes.error as error:
        if getattr(error, "winerror", None) == 1060:
            return {"installed": False, "service_name": SERVICE_NAME}
        raise
    state_names = {
        win32service.SERVICE_STOPPED: "stopped",
        win32service.SERVICE_START_PENDING: "start_pending",
        win32service.SERVICE_STOP_PENDING: "stop_pending",
        win32service.SERVICE_RUNNING: "running",
        win32service.SERVICE_CONTINUE_PENDING: "continue_pending",
        win32service.SERVICE_PAUSE_PENDING: "pause_pending",
        win32service.SERVICE_PAUSED: "paused",
    }
    return {
        "installed": True,
        "service_name": SERVICE_NAME,
        "state": state_names.get(int(raw[1]), f"unknown:{raw[1]}"),
        "pid_file": str(api_pid_path()),
        "config_directory": str(Path(service_token_path()).parent),
    }


def main() -> None:
    _require_windows()
    import servicemanager
    import win32serviceutil

    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(OctopusWindowsService)
        servicemanager.StartServiceCtrlDispatcher()  # type: ignore[no-untyped-call]
    else:
        win32serviceutil.HandleCommandLine(OctopusWindowsService)


if __name__ == "__main__":
    main()
