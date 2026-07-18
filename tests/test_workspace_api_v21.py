from __future__ import annotations

import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from octopus.api import create_app
from octopus.credentials import ResolvedCredential
from octopus.providers import ProviderCapabilities
from octopus.workspace_v2 import WorkspaceStore, create_workspace

TOKEN = "v21-test-token-that-is-long-enough"


def _wait_for_job(client: TestClient, headers: dict[str, str], job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/v2/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("V2 job did not finish")


@pytest.mark.parametrize(
    ("error_name", "expected_code"),
    [
        ("ProviderAuthError", "auth_failed"),
        ("ProviderQuotaError", "quota_exhausted"),
        ("ProviderRateLimitError", "rate_limited"),
        ("ProviderTransientError", "unavailable"),
        ("ProviderOutputError", "invalid_response"),
        ("RuntimeError", "invalid_configuration"),
    ],
)
def test_ai_connection_errors_have_actionable_codes(
    error_name: str,
    expected_code: str,
) -> None:
    import octopus.providers as provider_module
    import octopus.workspace_api_v2 as api_module

    error_type = getattr(provider_module, error_name, RuntimeError)
    code, message = api_module._ai_error(error_type("test failure"))

    assert code == expected_code
    assert message


def test_v21_api_sources_research_export_changes_and_jobs(tmp_path: Path) -> None:
    raw = tmp_path / "research"
    raw.mkdir()
    source = raw / "notes.txt"
    source.write_text("研究方法与可核验证据", encoding="utf-8")
    workspace = create_workspace(raw, "Research")
    store = WorkspaceStore(workspace)
    store.sync()
    document = store.list_documents()[0]
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        health = client.get("/v2/health")
        assert health.status_code == 200
        assert health.json()["contract_version"] == "2.0"
        assert client.get("/v2/openapi.json", headers=headers).status_code == 200

        listed = client.get("/v2/workspaces", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["workspace_id"] == workspace.workspace_id
        detail = client.get(f"/v2/workspaces/{workspace.workspace_id}", headers=headers)
        assert detail.status_code == 200

        sync = client.post(f"/v2/workspaces/{workspace.workspace_id}/sync", headers=headers)
        assert sync.status_code == 202
        sync_job = _wait_for_job(client, headers, sync.json()["job_id"])
        assert sync_job["status"] == "succeeded"

        documents = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/documents",
            headers=headers,
        )
        assert documents.status_code == 200
        assert documents.json()[0]["document_id"] == document.document_id
        document_url = (
            f"/v2/workspaces/{workspace.workspace_id}/documents/{document.document_id}"
        )
        assert client.get(document_url, headers=headers).status_code == 200
        members = client.get(f"{document_url}/members", headers=headers)
        assert members.status_code == 200
        assert members.json() == []
        content = client.get(f"{document_url}/content", headers=headers)
        assert content.status_code == 200
        assert content.content == source.read_bytes()
        open_target = client.post(f"{document_url}/open-target", headers=headers)
        assert open_target.status_code == 200
        assert open_target.json()["display_name"] == "notes.txt"
        assert open_target.json()["uri"].endswith("notes.txt")

        missing = (
            f"/v2/workspaces/{workspace.workspace_id}/documents/missing-document"
        )
        assert client.get(missing, headers=headers).status_code == 404
        assert client.get(f"{missing}/members", headers=headers).status_code == 404
        assert client.get(f"{missing}/content", headers=headers).status_code == 404
        assert client.post(f"{missing}/open-target", headers=headers).status_code == 404
        assert client.get(f"{missing}/pages/1/preview", headers=headers).status_code == 404

        ai_status = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/ai-index",
            headers=headers,
        )
        assert ai_status.status_code == 200
        assert ai_status.json()["document_count"] == 1
        ai_job = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/ai-index",
            headers=headers,
            json={"limit": 1},
        )
        assert ai_job.status_code == 202
        assert _wait_for_job(client, headers, ai_job.json()["job_id"])["status"] == "failed"

        templates = client.get("/v2/task-templates", headers=headers)
        assert templates.status_code == 200
        assert {item["template_id"] for item in templates.json()} == {
            "literature_review",
            "course_report",
            "free_research",
        }
        created = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/tasks",
            headers=headers,
            json={
                "title": "Research Pack",
                "goal": "整理证据",
                "template_id": "literature_review",
            },
        )
        assert created.status_code == 201
        task = created.json()
        task["items"] = [
            {
                "item_id": str(uuid.uuid4()),
                "document_id": document.document_id,
                "content_hash": document.content_hash,
                "verified_content_hash": document.content_hash,
                "name": document.name,
                "relative_path": document.relative_path,
                "excerpt": "可核验证据",
                "rationale": "研究方法",
                "slot_id": task["slots"][0]["slot_id"],
                "review_state": "confirmed",
                "source_status": "resolved",
                "freshness_status": "current",
                "position": 0,
                "citation": {
                    "citation_id": "notes",
                    "citation_type": "report",
                    "title": "Research Notes",
                    "authors": ["Ada Lovelace"],
                    "year": "2026",
                    "confidence": 1.0,
                },
            }
        ]
        saved = client.put(
            f"/v2/workspaces/{workspace.workspace_id}/tasks/{task['task_id']}",
            headers=headers,
            json={"expected_revision": task["revision"], "task": task},
        )
        assert saved.status_code == 200
        task = saved.json()
        task_url = f"/v2/workspaces/{workspace.workspace_id}/tasks/{task['task_id']}"
        assert client.get(task_url, headers=headers).status_code == 200
        task_list = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/tasks",
            headers=headers,
        )
        assert task_list.status_code == 200
        assert task_list.json()[0]["item_count"] == 1

        revalidated = client.post(
            f"{task_url}/revalidate",
            headers=headers,
            json={"expected_revision": task["revision"]},
        )
        assert revalidated.status_code == 200
        task = revalidated.json()
        exported = client.post(
            f"{task_url}/export",
            headers=headers,
            json={"citation_style": "apa", "include_sources": False},
        )
        assert exported.status_code == 200
        assert exported.content.startswith(b"PK")
        assert "application/zip" in exported.headers["content-type"]

        export_job_response = client.post(
            f"{task_url}/exports",
            headers=headers,
            json={"citation_style": "apa", "include_sources": True},
        )
        assert export_job_response.status_code == 202
        export_job = _wait_for_job(
            client,
            headers,
            export_job_response.json()["job_id"],
        )
        assert export_job["status"] == "succeeded"
        artifact = export_job["result"]
        assert isinstance(artifact, dict)
        assert artifact["included_source_count"] == 1
        artifact_response = client.get(
            (
                f"/v2/workspaces/{workspace.workspace_id}/exports/"
                f"{artifact['artifact_id']}"
            ),
            headers=headers,
        )
        assert artifact_response.status_code == 200
        assert artifact_response.content.startswith(b"PK")
        assert artifact_response.headers["x-octopus-artifact-id"] == artifact["artifact_id"]
        assert artifact_response.headers["x-octopus-artifact-sha256"] == artifact["sha256"]
        missing_artifact = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/exports/not-an-artifact",
            headers=headers,
        )
        assert missing_artifact.status_code == 404

        changes = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/changes",
            headers=headers,
            params={"include_acknowledged": True},
        )
        assert changes.status_code == 200
        assert isinstance(changes.json(), list)

        proposal = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/task-proposals",
            headers=headers,
            json={"goal": "研究方法", "template_id": "free_research"},
        )
        assert proposal.status_code in {422, 503}
        settings_test = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/ai-settings/test",
            headers=headers,
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "enabled": False,
            },
        )
        assert settings_test.status_code == 200
        assert settings_test.json()["code"] == "key_not_configured"
        settings_conflict = client.put(
            f"/v2/workspaces/{workspace.workspace_id}/ai-settings",
            headers=headers,
            json={
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-chat",
                "api_key": "secret",
                "clear_api_key": True,
            },
        )
        assert settings_conflict.status_code == 422

        assert client.get("/v2/jobs/missing", headers=headers).status_code == 404
        assert client.post("/v2/jobs/missing/cancel", headers=headers).status_code == 404
        cancel_finished = client.post(
            f"/v2/jobs/{sync_job['job_id']}/cancel",
            headers=headers,
        )
        assert cancel_finished.status_code == 200

        archived = client.post(
            f"{task_url}/archive",
            headers=headers,
            json={"expected_revision": task["revision"]},
        )
        assert archived.status_code == 200
        assert archived.json()["lifecycle"] == "archived"


def test_async_research_and_proposal_jobs_keep_retry_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import octopus.workspace_api_v2 as api_module

    raw = tmp_path / "async-research"
    raw.mkdir()
    workspace = create_workspace(raw, "Async Research")
    progress_events: list[dict[str, object]] = []

    def fake_research(
        workspace_id: str,
        question: str,
        progress,
        *,
        limit: int,
        search_options: dict[str, object],
    ) -> dict[str, object]:
        assert workspace_id == workspace.workspace_id
        assert question == "本地证据如何支持结论"
        assert limit == 12
        assert search_options["extensions"] == [".pdf"]
        progress({"phase": "retrieving", "completed": 1, "total": 2})
        progress_events.append({"question": question})
        return {
            "answer": "结论来自本地证据 [R1]",
            "citations": [{"citation_id": "R1"}],
            "results": [],
        }

    class FakeProposal:
        candidates = [SimpleNamespace(candidate_id="candidate-1")]

        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "proposal_id": "proposal-1",
                "workspace_id": workspace.workspace_id,
                "candidates": [{"candidate_id": "candidate-1"}],
                "slots": [],
            }

    monkeypatch.setattr(api_module, "run_workspace_research", fake_research)
    monkeypatch.setattr(
        api_module,
        "create_research_proposal",
        lambda workspace_id, goal, title, template_id: FakeProposal(),
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        research_response = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/research",
            headers=headers,
            json={
                "question": "本地证据如何支持结论",
                "limit": 12,
                "extensions": [".pdf"],
                "readability": ["readable"],
                "indexing_states": ["indexed"],
                "source_kinds": ["physical"],
            },
        )
        assert research_response.status_code == 202
        research_job = _wait_for_job(
            client,
            headers,
            research_response.json()["job_id"],
        )
        assert research_job["status"] == "succeeded"
        assert research_job["result"]["answer"] == "结论来自本地证据 [R1]"
        assert research_job["result"]["progress"]["retry_payload"]["kind"] == (
            "workspace_research"
        )
        assert progress_events == [{"question": "本地证据如何支持结论"}]

        proposal_response = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/task-proposals/jobs",
            headers=headers,
            json={
                "goal": "整理本地证据",
                "title": "证据提案",
                "template_id": "free_research",
            },
        )
        assert proposal_response.status_code == 202
        proposal_job = _wait_for_job(
            client,
            headers,
            proposal_response.json()["job_id"],
        )
        assert proposal_job["status"] == "succeeded"
        assert proposal_job["result"]["proposal"]["proposal_id"] == "proposal-1"
        proposal_progress = proposal_job["result"]["progress"]
        assert proposal_progress["phase"] == "completed"
        assert proposal_progress["evidence_count"] == 1
        assert proposal_progress["retry_payload"]["kind"] == "task_proposal"


def test_ai_settings_persist_glm_capabilities_and_test_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import octopus.workspace_api_v2 as api_module

    raw = tmp_path / "ai-settings"
    raw.mkdir()
    workspace = create_workspace(raw, "AI Settings")
    stored: list[tuple[str, str, str]] = []
    monkeypatch.setattr(api_module, "read_stored_ai_api_key", lambda _: "previous-key")
    monkeypatch.setattr(
        api_module,
        "save_stored_ai_api_key",
        lambda workspace_id, provider, api_key: stored.append(
            (workspace_id, provider, api_key)
        ),
    )
    monkeypatch.setattr(api_module, "delete_stored_ai_api_key", lambda _: None)
    monkeypatch.setattr(
        api_module,
        "resolve_ai_api_key",
        lambda workspace_id, provider: ResolvedCredential("configured-key", "test"),
    )
    monkeypatch.setattr(
        api_module,
        "test_ai_connection",
        lambda config, api_key: ProviderCapabilities(
            text=True,
            structured_output=True,
            vision=True,
            file_upload=False,
        ),
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        initial = client.get(
            f"/v2/workspaces/{workspace.workspace_id}/ai-settings",
            headers=headers,
        )
        assert initial.status_code == 200
        assert initial.json()["credential_configured"] is True

        updated = client.put(
            f"/v2/workspaces/{workspace.workspace_id}/ai-settings",
            headers=headers,
            json={
                "enabled": True,
                "provider": "openai_compatible",
                "preset": "glm",
                "base_url": "https://unused.example/v1",
                "model": "glm-confirmed-model",
                "api_key": "new-key",
                "tested_capabilities": {
                    "text": True,
                    "structured_output": True,
                    "vision": True,
                    "file_upload": False,
                },
            },
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["preset"] == "glm"
        assert payload["provider"] == "openai_compatible"
        assert payload["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
        assert payload["capabilities"]["vision"] is True
        assert stored == [
            (workspace.workspace_id, "openai_compatible", "new-key")
        ]

        tested = client.post(
            f"/v2/workspaces/{workspace.workspace_id}/ai-settings/test",
            headers=headers,
            json={
                "enabled": True,
                "provider": "openai_compatible",
                "preset": "glm",
                "base_url": "https://unused.example/v1",
                "model": "glm-confirmed-model",
                "api_key": "new-key",
            },
        )
        assert tested.status_code == 200
        assert tested.json() == {
            "ok": True,
            "code": "connected",
            "message": "已连接 glm-confirmed-model。",
            "capabilities": {
                "text": True,
                "structured_output": True,
                "vision": True,
                "file_upload": False,
            },
        }
