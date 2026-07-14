from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol, cast

from .api import API_CONTRACT_VERSION
from .service_control import (
    api_status,
    ensure_service_token,
    start_api_process,
)


class DesktopServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def recovery_guidance(error: DesktopServiceError) -> str:
    if error.code == "service_unavailable":
        return "本地服务未启动。请点击“重新连接服务”；资料与索引不会受影响。"
    if error.status_code == 401:
        return "本地凭据已变化。请重启桌面端以重新读取服务凭据。"
    if error.status_code == 409:
        return "仓库正在执行其他任务。请等待当前任务完成后重试。"
    if error.status_code == 422:
        return "请求无法执行。请检查目录、迁移提示或互斥选项。"
    return "操作未完成。可刷新状态、查看技术详情后重试。"


class DesktopApi(Protocol):
    def contract(self) -> dict[str, Any]: ...

    def repositories(self) -> list[dict[str, Any]]: ...

    def repository(self, repository_id: str) -> dict[str, Any]: ...

    def create_repository(
        self, raw_path: str, index_path: str, name: str | None = None
    ) -> dict[str, Any]: ...

    def search(
        self, repository_id: str, query: str, *, auto_mode: bool = False
    ) -> dict[str, Any]: ...

    def submit_update(
        self, repository_id: str, *, retry_only: bool = False
    ) -> dict[str, Any]: ...

    def rebuild_search(self, repository_id: str) -> dict[str, Any]: ...

    def validate(self, repository_id: str) -> dict[str, Any]: ...

    def latest_report(self, repository_id: str) -> dict[str, Any] | None: ...

    def migrations(self) -> dict[str, Any]: ...

    def create_diagnostics(
        self, output_path: str, repository_ids: list[str]
    ) -> dict[str, Any]: ...

    def job(self, job_id: str) -> dict[str, Any]: ...


class LocalApiClient:
    def __init__(self, base_url: str, token: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    @classmethod
    def from_runtime(cls, *, start_if_needed: bool = True) -> LocalApiClient:
        status = api_status()
        if not status.get("running") or not status.get("healthy"):
            if not start_if_needed:
                raise DesktopServiceError("service_unavailable", "Octopus Local API is unavailable")
            try:
                status = start_api_process()
            except (OSError, RuntimeError, ValueError) as error:
                raise DesktopServiceError("service_unavailable", str(error)) from error
        host = str(status.get("host", "127.0.0.1"))
        port = int(status.get("port", 8765))
        url_host = f"[{host}]" if ":" in host else host
        return cls(f"http://{url_host}:{port}", ensure_service_token())

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.reason
            try:
                response = json.loads(error.read().decode("utf-8"))
                detail = response.get("detail", detail) if isinstance(response, dict) else detail
            except (UnicodeError, json.JSONDecodeError):
                pass
            raise DesktopServiceError(
                "api_error",
                str(detail),
                status_code=error.code,
            ) from error
        except (OSError, TimeoutError, urllib.error.URLError) as error:
            raise DesktopServiceError("service_unavailable", str(error)) from error

    def contract(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._request("GET", "/v1/contract"))

    def repositories(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self._request("GET", "/v1/repositories"))

    def repository(self, repository_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], self._request("GET", f"/v1/repositories/{repository_id}"))

    def create_repository(
        self, raw_path: str, index_path: str, name: str | None = None
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                "/v1/repositories",
                {
                    "raw_path": raw_path,
                    "index_path": index_path,
                    "name": name,
                    "build": True,
                },
            ),
        )

    def search(
        self, repository_id: str, query: str, *, auto_mode: bool = False
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                f"/v1/repositories/{repository_id}/search",
                {"query": query, "mode": "auto" if auto_mode else "local", "limit": 20},
            ),
        )

    def submit_update(
        self, repository_id: str, *, retry_only: bool = False
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                f"/v1/repositories/{repository_id}/updates",
                {"retry_only": retry_only},
            ),
        )

    def rebuild_search(self, repository_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request("POST", f"/v1/repositories/{repository_id}/rebuild-search", {}),
        )

    def validate(self, repository_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request("POST", f"/v1/repositories/{repository_id}/validate", {}),
        )

    def latest_report(self, repository_id: str) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                self._request("GET", f"/v1/repositories/{repository_id}/reports/latest"),
            )
        except DesktopServiceError as error:
            if error.status_code == 404:
                return None
            raise

    def migrations(self) -> dict[str, Any]:
        return cast(dict[str, Any], self._request("GET", "/v1/migrations"))

    def create_diagnostics(
        self, output_path: str, repository_ids: list[str]
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                "/v1/diagnostics",
                {"output_path": output_path, "repository_ids": repository_ids},
            ),
        )

    def job(self, job_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], self._request("GET", f"/v1/jobs/{job_id}"))


class DesktopController:
    def __init__(self, api: DesktopApi) -> None:
        self.api = api
        self.repositories: list[dict[str, Any]] = []
        self.selected_repository_id = ""
        self.repository_state: dict[str, Any] = {}
        self.search_report: dict[str, Any] = {}

    def connect(self) -> None:
        contract = self.api.contract()
        if contract.get("contract_version") != API_CONTRACT_VERSION:
            raise DesktopServiceError(
                "contract_mismatch",
                f"Desktop requires Local API contract {API_CONTRACT_VERSION}",
            )
        self.refresh_repositories()

    def refresh_repositories(self) -> list[dict[str, Any]]:
        self.repositories = self.api.repositories()
        identifiers = {str(item.get("repository_id", "")) for item in self.repositories}
        if self.selected_repository_id not in identifiers:
            self.selected_repository_id = next(iter(identifiers), "")
        if self.selected_repository_id:
            self.refresh_selected()
        else:
            self.repository_state = {}
        return self.repositories

    def refresh_selected(self) -> dict[str, Any]:
        if not self.selected_repository_id:
            self.repository_state = {}
            return self.repository_state
        self.repository_state = self.api.repository(self.selected_repository_id)
        return self.repository_state

    def select(self, repository_id: str) -> dict[str, Any]:
        self.selected_repository_id = repository_id
        return self.refresh_selected()

    def create(self, raw_path: str, index_path: str, name: str | None = None) -> dict[str, Any]:
        created = self.api.create_repository(raw_path, index_path, name)
        repository = cast(dict[str, Any], created["repository"])
        self.selected_repository_id = str(repository["repository_id"])
        self.refresh_repositories()
        return created

    def search(self, query: str, *, auto_mode: bool = False) -> dict[str, Any]:
        if not self.selected_repository_id:
            raise DesktopServiceError("no_repository", "No repository is selected")
        self.search_report = self.api.search(
            self.selected_repository_id, query, auto_mode=auto_mode
        )
        return self.search_report

    def submit_update(self, *, retry_only: bool = False) -> dict[str, Any]:
        if not self.selected_repository_id:
            raise DesktopServiceError("no_repository", "No repository is selected")
        return self.api.submit_update(self.selected_repository_id, retry_only=retry_only)

    def rebuild_search(self) -> dict[str, Any]:
        if not self.selected_repository_id:
            raise DesktopServiceError("no_repository", "No repository is selected")
        return self.api.rebuild_search(self.selected_repository_id)

    def validate(self) -> dict[str, Any]:
        if not self.selected_repository_id:
            raise DesktopServiceError("no_repository", "No repository is selected")
        return self.api.validate(self.selected_repository_id)

    def latest_report(self) -> dict[str, Any] | None:
        if not self.selected_repository_id:
            return None
        return self.api.latest_report(self.selected_repository_id)

    def migrations(self) -> dict[str, Any]:
        return self.api.migrations()

    def create_diagnostics(self, output_path: str) -> dict[str, Any]:
        if not self.selected_repository_id:
            raise DesktopServiceError("no_repository", "No repository is selected")
        return self.api.create_diagnostics(output_path, [self.selected_repository_id])

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float = 120.0,
        on_change: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.api.job(job_id)
            if on_change:
                on_change(job)
            if job.get("status") in {"succeeded", "failed"}:
                return job
            time.sleep(0.1)
        raise DesktopServiceError("job_timeout", f"Job {job_id} did not finish")

    def status_summary(self) -> str:
        states = cast(dict[str, int], self.repository_state.get("states", {}))
        pending = sum(states.get(name, 0) for name in ("pending_edit", "pending_stable"))
        failed = sum(states.get(name, 0) for name in ("failed", "retry"))
        orphaned = states.get("orphaned", 0)
        return f"待稳定 {pending} · 失败/重试 {failed} · 已孤立 {orphaned}"
