from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octopus import __version__
from octopus import api as api_module
from octopus.api import create_app
from octopus.config import load_repository_config, repository_config_path
from octopus.credentials import ResolvedCredential
from octopus.engine import UpdateEngine
from octopus.providers import ProviderAuthError

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
        ui = client.get("/ui/")
        assert ui.status_code == 200
        assert "default-src 'self'" in ui.headers["content-security-policy"]
        assert "img-src 'self' data: blob:" in ui.headers["content-security-policy"]
        assert "http://" not in ui.text and "https://" not in ui.text
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
        assert search.json()["requested_mode"] == "local"
        assert search.json()["actual_mode"] == "local"
        assert search.json()["results"]
        assert search.json()["results"][0]["match_reasons"]
        assert search.json()["results"][0]["match_evidence"]
        assert search.json()["results"][0]["open_target_uri"].startswith("file:")
        filtered = client.post(
            f"/v1/repositories/{repository_id}/search",
            headers=headers,
            json={
                "query": "local API",
                "filters": {"index_types": ["text"], "path_prefix": "roadmap"},
            },
        )
        assert filtered.status_code == 200
        assert [item["name"] for item in filtered.json()["results"]] == ["roadmap.txt"]
        assert filtered.json()["results"][0]["content_id"]
        assert filtered.json()["results"][0]["modified_at"]
        assert filtered.json()["results"][0]["size_bytes"] > 0
        legacy_auto = client.post(
            f"/v1/repositories/{repository_id}/search",
            headers=headers,
            json={"query": "local API", "full": True},
        )
        assert legacy_auto.status_code == 200
        assert legacy_auto.json()["requested_mode"] == "auto"
        conflicting = client.post(
            f"/v1/repositories/{repository_id}/search",
            headers=headers,
            json={"query": "local API", "mode": "local", "full": False},
        )
        assert conflicting.status_code == 422

        created_pack = client.post(
            f"/v1/repositories/{repository_id}/task-packs",
            headers=headers,
            json={"title": "API review", "goal": "Review local API evidence"},
        )
        assert created_pack.status_code == 201
        pack = created_pack.json()
        result = filtered.json()["results"][0]
        item = {
            "item_id": "item-roadmap",
            "node_id": result["node_id"],
            "name": result["name"],
            "index_type": result["index_type"],
            "raw_relative_path": result["raw_relative_path"],
            "content_id": result["content_id"],
            "status_snapshot": result["status"],
            "anchors": result["evidence"],
            "rationale": "Primary result",
            "slot_id": pack["slots"][0]["slot_id"],
            "review_state": "pending",
            "position": 0,
        }
        pack["items"] = [item]
        updated_pack = client.put(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}",
            headers=headers,
            json={"expected_revision": pack["revision"], "task_pack": pack},
        )
        assert updated_pack.status_code == 200
        pack = updated_pack.json()
        stale_update = client.put(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}",
            headers=headers,
            json={"expected_revision": 1, "task_pack": pack},
        )
        assert stale_update.status_code == 409
        markdown = client.get(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}/markdown",
            headers=headers,
        )
        assert markdown.status_code == 200
        assert "# API review" in markdown.text
        assert "Primary result" in markdown.text
        rejected_package = client.post(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}/package",
            headers=headers,
            json={
                "output_path": str(index.parent / "rejected-package"),
                "confirmed_item_ids": ["item-roadmap"],
            },
        )
        assert rejected_package.status_code == 422

        pack["items"][0]["review_state"] = "confirmed"
        confirmed_pack = client.put(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}",
            headers=headers,
            json={"expected_revision": pack["revision"], "task_pack": pack},
        )
        assert confirmed_pack.status_code == 200
        pack = confirmed_pack.json()
        nonempty_path = index.parent / "nonempty-package"
        nonempty_path.mkdir()
        (nonempty_path / "keep.txt").write_text("keep", encoding="utf-8")
        nonempty_package = client.post(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}/package",
            headers=headers,
            json={
                "output_path": str(nonempty_path),
                "confirmed_item_ids": ["item-roadmap"],
            },
        )
        assert nonempty_package.status_code == 422
        assert (nonempty_path / "keep.txt").read_text(encoding="utf-8") == "keep"
        package_path = index.parent / "task-package"
        package = client.post(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}/package",
            headers=headers,
            json={
                "output_path": str(package_path),
                "confirmed_item_ids": ["item-roadmap"],
            },
        )
        assert package.status_code == 202
        package_job = _wait_for_job(client, headers, package.json()["job_id"])
        assert package_job["status"] == "succeeded", package_job
        assert (package_path / "package-manifest.json").is_file()

        archived = client.post(
            f"/v1/repositories/{repository_id}/task-packs/{pack['task_pack_id']}/archive",
            headers=headers,
            json={"expected_revision": pack["revision"]},
        )
        assert archived.status_code == 200
        assert archived.json()["lifecycle"] == "archived"
        assert client.get(
            f"/v1/repositories/{repository_id}/task-packs", headers=headers
        ).json() == []
        validation = client.post(f"/v1/repositories/{repository_id}/validate", headers=headers)
        assert validation.status_code == 200
        assert validation.json()["error_count"] == 0
        latest = client.get(f"/v1/repositories/{repository_id}/reports/latest", headers=headers)
        assert latest.status_code == 200
        assert latest.json()["version"] == __version__
        assert TOKEN not in json.dumps(client.get("/v1/jobs", headers=headers).json())
        diagnostic_path = index.parent / "api-diagnostics.zip"
        diagnostics = client.post(
            "/v1/diagnostics",
            headers=headers,
            json={"output_path": str(diagnostic_path), "repository_ids": [repository_id]},
        )
        assert diagnostics.status_code == 200
        assert diagnostics.json() == {
            "created": True,
            "file": "api-diagnostics.zip",
            "local_only": True,
            "uploaded": False,
        }
        assert diagnostic_path.exists()


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


def test_v1_contract_and_repository_creation_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    raw = tmp_path / "资料"
    index = tmp_path / "索引"
    raw.mkdir()
    source = raw / "read-only.txt"
    source.write_text("desktop API workflow", encoding="utf-8")
    before = source.read_bytes()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        preflight = client.post(
            "/v1/repositories/preflight",
            headers=headers,
            json={"raw_path": str(raw), "index_path": str(index)},
        )
        assert preflight.status_code == 200
        assert preflight.json()["file_count"] == 1
        assert preflight.json()["blockers"] == []
        contract = client.get("/v1/contract", headers=headers)
        assert contract.status_code == 200
        assert contract.json()["contract_version"] == "1.0"
        assert "local_diagnostics" in contract.json()["features"]
        assert "task_packs" in contract.json()["features"]
        created = client.post(
            "/v1/repositories",
            headers=headers,
            json={
                "raw_path": str(raw),
                "index_path": str(index),
                "name": "Desktop API",
                "build": True,
            },
        )
        assert created.status_code == 201, created.text
        payload = created.json()
        assert payload["repository"]["name"] == "Desktop API"
        job = _wait_for_job(client, headers, payload["job"]["job_id"])
        assert job["status"] == "succeeded"
        openapi = client.get("/v1/openapi.json", headers=headers).json()
        assert set(openapi["paths"]["/v1/repositories"]) == {"get", "post"}

    assert source.read_bytes() == before


def test_ai_settings_store_secret_outside_repository_config(
    repository: tuple[Path, Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, index, config = repository
    repository_id = config.repository.raw_repo_id
    headers = {"Authorization": f"Bearer {TOKEN}"}
    stored: dict[str, str] = {}
    tested: list[tuple[str, str]] = []

    monkeypatch.setattr(
        api_module,
        "read_stored_ai_api_key",
        lambda value: stored.get(value, ""),
    )
    monkeypatch.setattr(
        api_module,
        "save_stored_ai_api_key",
        lambda value, provider, key: stored.__setitem__(value, key),
    )
    monkeypatch.setattr(
        api_module,
        "delete_stored_ai_api_key",
        lambda value: stored.pop(value, None),
    )
    monkeypatch.setattr(
        api_module,
        "resolve_ai_api_key",
        lambda value, provider: ResolvedCredential(
            stored.get(value, ""),
            "windows_credential" if stored.get(value) else "none",
        ),
    )
    monkeypatch.setattr(
        api_module,
        "test_ai_connection",
        lambda candidate, key: tested.append((candidate.ai_policy.model, key)),
    )

    app = create_app(token=TOKEN, start_scheduler=False)
    with TestClient(app) as client:
        initial = client.get(
            f"/v1/repositories/{repository_id}/ai-settings",
            headers=headers,
        )
        assert initial.status_code == 200
        assert initial.json()["credential_configured"] is False

        missing = client.post(
            f"/v1/repositories/{repository_id}/ai-settings/test",
            headers=headers,
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-test",
            },
        )
        assert missing.json()["code"] == "key_not_configured"

        connection = client.post(
            f"/v1/repositories/{repository_id}/ai-settings/test",
            headers=headers,
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-test",
                "api_key": "private-test-key",
            },
        )
        assert connection.json()["ok"] is True
        assert tested == [("deepseek-test", "private-test-key")]
        assert stored == {}

        saved = client.put(
            f"/v1/repositories/{repository_id}/ai-settings",
            headers=headers,
            json={
                "enabled": True,
                "provider": "openai_compatible",
                "base_url": "https://models.example.com/v1",
                "model": "example-model",
                "api_key": "private-test-key",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["credential_configured"] is True
        assert "private-test-key" not in saved.text
        assert stored[repository_id] == "private-test-key"

        loaded = load_repository_config(index)
        assert loaded.ai_policy.enabled is True
        assert loaded.ai_policy.provider == "openai_compatible"
        assert loaded.ai_policy.base_url == "https://models.example.com/v1"
        assert loaded.ai_policy.model == "example-model"
        assert "private-test-key" not in repository_config_path(index).read_text(encoding="utf-8")

        monkeypatch.setattr(
            api_module,
            "test_ai_connection",
            lambda candidate, key: (_ for _ in ()).throw(ProviderAuthError("auth")),
        )
        rejected = client.post(
            f"/v1/repositories/{repository_id}/ai-settings/test",
            headers=headers,
            json={
                "provider": "openai_compatible",
                "base_url": "https://models.example.com/v1",
                "model": "example-model",
            },
        )
        assert rejected.json()["code"] == "auth_failed"

        cleared = client.put(
            f"/v1/repositories/{repository_id}/ai-settings",
            headers=headers,
            json={
                "enabled": False,
                "provider": "openai_compatible",
                "base_url": "https://models.example.com/v1",
                "model": "example-model",
                "clear_api_key": True,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["credential_configured"] is False
        assert stored == {}
