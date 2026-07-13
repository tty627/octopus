from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from octopus import __version__
from octopus.api import create_app
from octopus.config import repository_config_path
from octopus.engine import UpdateEngine

TOKEN = "test-token-that-is-long-enough-for-api-authentication"


def _wait_for_job(client: TestClient, headers: dict[str, str], job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("API job did not finish")


def test_local_api_auth_repository_search_and_jobs(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, config = repository
    repository_id = config.repository.raw_repo_id
    headers = {"Authorization": f"Bearer {TOKEN}"}
    (raw / "roadmap.txt").write_text("Octopus local API roadmap", encoding="utf-8")
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["version"] == __version__
        assert client.get("/v1/repositories").status_code == 401
        assert (
            client.get(
                "/v1/repositories", headers={"Authorization": "Bearer incorrect"}
            ).status_code
            == 401
        )

        repositories = client.get("/v1/repositories", headers=headers)
        assert repositories.status_code == 200
        assert repositories.json()[0]["repository_id"] == repository_id
        assert client.get("/v1/openapi.json", headers=headers).status_code == 200

        submitted = client.post(
            f"/v1/repositories/{repository_id}/updates",
            headers=headers,
            json={"dry_run": True, "force_path": "*"},
        )
        assert submitted.status_code == 202
        job = _wait_for_job(client, headers, submitted.json()["job_id"])
        assert job["status"] == "succeeded"
        assert "text_updates" in job["result"]

        UpdateEngine(index).run(force_path="*")
        search = client.post(
            f"/v1/repositories/{repository_id}/search",
            headers=headers,
            json={"query": "local API"},
        )
        assert search.status_code == 200
        assert search.json()
        validation = client.post(f"/v1/repositories/{repository_id}/validate", headers=headers)
        assert validation.status_code == 200
        assert validation.json()["error_count"] == 0
        latest = client.get(f"/v1/repositories/{repository_id}/reports/latest", headers=headers)
        assert latest.status_code == 200
        assert latest.json()["version"] == __version__
        assert TOKEN not in json.dumps(client.get("/v1/jobs", headers=headers).json())


def test_api_rejects_invalid_update_flags_and_missing_resources(
    repository: tuple[Path, Path, object],
) -> None:
    _, _, config = repository
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)
    with TestClient(app) as client:
        invalid = client.post(
            f"/v1/repositories/{config.repository.raw_repo_id}/updates",
            headers=headers,
            json={"leaf_only": True, "foldernode_only": True},
        )
        assert invalid.status_code == 422
        assert client.get("/v1/repositories/missing", headers=headers).status_code == 404
        assert client.get("/v1/jobs/missing", headers=headers).status_code == 404


def test_repository_listing_marks_invalid_repository_unavailable(
    repository: tuple[Path, Path, object],
) -> None:
    _, index, _ = repository
    repository_config_path(index).write_text("{}", encoding="utf-8")
    app = create_app(token=TOKEN, start_scheduler=False)
    with TestClient(app) as client:
        response = client.get("/v1/repositories", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json()[0]["available"] is False
