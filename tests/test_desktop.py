from __future__ import annotations

import inspect
from typing import Any

import pytest

import octopus.desktop as desktop_module
from octopus.api import API_CONTRACT_VERSION
from octopus.desktop import DESKTOP_SHORTCUTS, desktop_scale
from octopus.desktop_client import (
    DesktopController,
    DesktopServiceError,
    LocalApiClient,
    recovery_guidance,
)


class FakeDesktopApi:
    def __init__(self) -> None:
        self.retry_flags: list[bool] = []
        self.jobs = [
            {"job_id": "job-1", "status": "queued"},
            {"job_id": "job-1", "status": "succeeded", "result": {"leaf_updated": 1}},
        ]
        self.items = [
            {
                "repository_id": "repo-1",
                "name": "桌面测试",
                "available": True,
            }
        ]

    def contract(self) -> dict[str, Any]:
        return {"contract_version": API_CONTRACT_VERSION}

    def repositories(self) -> list[dict[str, Any]]:
        return self.items

    def repository(self, repository_id: str) -> dict[str, Any]:
        return {
            "repository_id": repository_id,
            "name": "桌面测试",
            "states": {
                "pending_edit": 2,
                "pending_stable": 1,
                "failed": 1,
                "retry": 1,
                "orphaned": 3,
            },
        }

    def create_repository(
        self, raw_path: str, index_path: str, name: str | None = None
    ) -> dict[str, Any]:
        created = {
            "repository_id": "repo-2",
            "name": name or "新仓库",
            "available": True,
        }
        self.items.append(created)
        return {"repository": created, "job": {"job_id": "job-1", "status": "queued"}}

    def search(
        self, repository_id: str, query: str, *, auto_mode: bool = False
    ) -> dict[str, Any]:
        return {
            "query": query,
            "actual_mode": "ai" if auto_mode else "local",
            "results": [
                {
                    "name": "目标.docx",
                    "raw_relative_path": "目标.docx",
                    "open_target_uri": "file:///target",
                }
            ],
        }

    def submit_update(
        self, repository_id: str, *, retry_only: bool = False
    ) -> dict[str, Any]:
        self.retry_flags.append(retry_only)
        return {"job_id": "job-1", "status": "queued"}

    def rebuild_search(self, repository_id: str) -> dict[str, Any]:
        return {"job_id": "job-1", "status": "queued"}

    def validate(self, repository_id: str) -> dict[str, Any]:
        return {"error_count": 0, "warning_count": 0}

    def latest_report(self, repository_id: str) -> dict[str, Any] | None:
        return {"status": "success", "ai_usage": {"calls": 0, "total_tokens": 0}}

    def migrations(self) -> dict[str, Any]:
        return {"required": False, "repositories": []}

    def job(self, job_id: str) -> dict[str, Any]:
        return self.jobs.pop(0)


def test_desktop_controller_covers_repository_search_and_recovery_workflows() -> None:
    api = FakeDesktopApi()
    controller = DesktopController(api)

    controller.connect()
    assert controller.selected_repository_id == "repo-1"
    assert controller.status_summary() == "待稳定 3 · 失败/重试 2 · 已孤立 3"
    assert controller.search("目标")["results"][0]["open_target_uri"] == "file:///target"
    assert controller.search("目标", auto_mode=True)["actual_mode"] == "ai"
    assert controller.submit_update()["job_id"] == "job-1"
    assert controller.submit_update(retry_only=True)["job_id"] == "job-1"
    assert api.retry_flags == [False, True]
    assert controller.rebuild_search()["job_id"] == "job-1"
    assert controller.validate()["error_count"] == 0
    assert controller.latest_report()["status"] == "success"  # type: ignore[index]
    assert controller.migrations()["required"] is False
    assert controller.wait_for_job("job-1", timeout=1)["status"] == "succeeded"

    created = controller.create("C:/资料", "C:/索引", "新仓库")
    assert created["repository"]["repository_id"] == "repo-2"
    assert controller.selected_repository_id == "repo-2"


def test_desktop_contract_mismatch_and_actionable_errors() -> None:
    api = FakeDesktopApi()
    api.contract = lambda: {"contract_version": "9.0"}  # type: ignore[method-assign]
    with pytest.raises(DesktopServiceError, match="requires Local API contract"):
        DesktopController(api).connect()

    unavailable = DesktopServiceError("service_unavailable", "offline")
    locked = DesktopServiceError("api_error", "locked", status_code=409)
    migration = DesktopServiceError("api_error", "migration", status_code=422)
    assert "重新连接服务" in recovery_guidance(unavailable)
    assert "等待当前任务" in recovery_guidance(locked)
    assert "迁移提示" in recovery_guidance(migration)


def test_runtime_client_starts_service_and_uses_current_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "octopus.desktop_client.api_status",
        lambda: {"running": False},
    )
    monkeypatch.setattr(
        "octopus.desktop_client.start_api_process",
        lambda: {"running": True, "healthy": True, "host": "127.0.0.1", "port": 9876},
    )
    monkeypatch.setattr("octopus.desktop_client.ensure_service_token", lambda: "token")

    client = LocalApiClient.from_runtime()

    assert client.base_url == "http://127.0.0.1:9876"
    assert client.token == "token"


def test_desktop_accessibility_contract_has_keyboard_and_high_dpi_support() -> None:
    assert DESKTOP_SHORTCUTS == {
        "focus_search": "<Control-f>",
        "add_repository": "<Control-n>",
        "refresh": "<F5>",
    }
    assert desktop_scale(96.0) == pytest.approx(4 / 3)
    assert desktop_scale(192.0) == pytest.approx(8 / 3)


def test_desktop_presentation_layer_does_not_import_repository_core() -> None:
    source = inspect.getsource(desktop_module)
    assert "from .engine import" not in source
    assert "from .search import" not in source
    assert "from .config import" not in source
