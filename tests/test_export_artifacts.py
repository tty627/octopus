from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from octopus.api import create_app
from octopus.export_artifacts import register_export_artifact, resolve_export_artifact
from octopus.utils import atomic_write_json, load_json
from octopus.workspace_v2 import create_workspace

TOKEN = "test-token-that-is-long-enough-for-export-artifacts"


def _bundle(path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("research.md", "# Research")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": "1.1",
                    "items": [
                        {
                            "review_state": "pending",
                            "freshness_status": "changed",
                            "source_status": "unavailable",
                            "included_source": False,
                        }
                    ],
                }
            ),
        )
    return path


def test_export_artifact_rejects_invalid_expired_and_tampered_files(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    workspace = create_workspace(raw, "Exports")
    source = _bundle(tmp_path / "research.zip")

    artifact = register_export_artifact(workspace.workspace_id, source)
    resolved, path = resolve_export_artifact(workspace.workspace_id, artifact.artifact_id)
    assert resolved.sha256 == artifact.sha256
    assert resolved.included_source_count == 0
    assert resolved.skipped_source_count == 1
    assert len(resolved.warnings) == 3
    assert path.read_bytes() == source.read_bytes()

    with pytest.raises(FileNotFoundError, match="not found"):
        resolve_export_artifact(workspace.workspace_id, "../research.zip")

    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        resolve_export_artifact(workspace.workspace_id, artifact.artifact_id)

    expiring = register_export_artifact(workspace.workspace_id, source)
    record = path.parent / f"{expiring.artifact_id}.json"
    payload = load_json(record, {})
    payload["expires_at"] = "2000-01-01T00:00:00+00:00"
    atomic_write_json(record, payload)
    with pytest.raises(FileNotFoundError, match="expired"):
        resolve_export_artifact(workspace.workspace_id, expiring.artifact_id)
    assert not record.exists()
    assert not record.with_suffix(".zip").exists()


def test_async_export_returns_a_real_downloadable_zip(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    workspace = create_workspace(raw, "API Exports")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        task_response = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/tasks",
            headers=headers,
            json={"title": "研究包", "goal": "验证导出"},
        )
        assert task_response.status_code == 201
        task = task_response.json()
        started = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/tasks/{task['task_id']}/exports",
            headers=headers,
            json={"citation_style": "gb-t-7714-2015", "include_sources": False},
        )
        assert started.status_code == 202

        deadline = time.monotonic() + 10
        job: dict[str, object] = {}
        while time.monotonic() < deadline:
            job = client.get(
                f"/v2/jobs/{started.json()['job_id']}",
                headers=headers,
            ).json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded", job
        result = job["result"]
        assert isinstance(result, dict)
        artifact_id = str(result["artifact_id"])

        downloaded = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/exports/{artifact_id}",
            headers=headers,
        )
        assert downloaded.status_code == 200
        assert downloaded.headers["x-octopus-artifact-id"] == artifact_id
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert {"manifest.json", "research.md"} <= set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["schema_version"] == "1.1"

        invalid = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/exports/not-an-artifact",
            headers=headers,
        )
        assert invalid.status_code == 404
