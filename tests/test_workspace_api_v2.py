from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from octopus.api import create_app
from octopus.workspace_v2 import ExtractedPage, ExtractedSource

TOKEN = "test-token-that-is-long-enough-for-v2-api"


def _wait_for_v2_job(
    client: TestClient,
    headers: dict[str, str],
    job_id: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/v2/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"succeeded", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("V2 API job did not finish")


def test_v2_workspace_search_preview_tasks_and_read_only_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from octopus import workspace_v2

    raw = tmp_path / "高数"
    raw.mkdir()
    notes = raw / "微分方程coursenotes.txt"
    notes.write_text("第六章 微分方程\n一阶微分方程与高阶线性微分方程。", encoding="utf-8")
    pdf = raw / "09 级数.pdf"
    Image.new("RGB", (180, 240), "white").save(pdf, "PDF")
    before = {path.name: path.read_bytes() for path in raw.iterdir()}
    original_extract = workspace_v2.extract_source

    def extract(path: Path) -> ExtractedSource:
        if path.suffix.casefold() != ".pdf":
            return original_extract(path)
        return ExtractedSource(
            title="级数",
            page_count=1,
            pages=[
                ExtractedPage(
                    page_number=1,
                    text="级数 series 数项级数 幂级数 收敛判别法",
                    extraction_method="pdfium",
                    quality_score=0.92,
                )
            ],
        )

    monkeypatch.setattr(workspace_v2, "extract_source", extract)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(token=TOKEN, start_scheduler=False)

    with TestClient(app) as client:
        assert client.get("/v2/workspaces").status_code == 401
        contract = client.get("/v2/contract", headers=headers)
        assert contract.status_code == 200
        assert contract.json()["contract_version"] == "2.0"

        created = client.post(
            "/v2/workspaces",
            headers=headers,
            json={"raw_path": str(raw), "name": "高数"},
        )
        assert created.status_code == 201, created.text
        assert "index_path" not in created.text
        workspace = created.json()["workspace"]
        workspace_id = workspace["workspace_id"]
        job = _wait_for_v2_job(client, headers, created.json()["job"]["job_id"])
        assert job["status"] == "succeeded", job

        search = client.post(
            f"/v2/workspaces/{workspace_id}/search",
            headers=headers,
            json={"query": "微分方程", "mode": "local"},
        )
        assert search.status_code == 200
        result = search.json()["results"][0]
        assert result["name"] == "微分方程coursenotes.txt"
        assert result["best_evidence"]["reason"] == "文件名包含查询内容"
        assert not {"exact_name", "folder_child", "index_path", "node_id"} & set(result)

        series = client.post(
            f"/v2/workspaces/{workspace_id}/search",
            headers=headers,
            json={"query": "级数"},
        ).json()["results"][0]
        assert series["name"] == "09 级数.pdf"
        assert "锟" not in series["best_evidence"]["excerpt"]
        preview_url = (
            f"/v2/workspaces/{workspace_id}/documents/{series['document_id']}"
            f"/pages/1/preview"
        )
        assert client.get(preview_url).status_code == 401
        preview = client.get(preview_url, headers=headers)
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "image/png"
        assert preview.content.startswith(b"\x89PNG")

        created_task = client.post(
            f"/v2/workspaces/{workspace_id}/tasks",
            headers=headers,
            json={"title": "复习清单", "goal": "整理页面证据"},
        )
        assert created_task.status_code == 201
        task = created_task.json()
        task["items"] = [
            {
                "item_id": str(uuid.uuid4()),
                "document_id": series["document_id"],
                "content_hash": series["content_hash"],
                "name": series["name"],
                "relative_path": series["relative_path"],
                "page_number": 1,
                "excerpt": series["best_evidence"]["excerpt"],
                "rationale": "级数章节证据",
                "slot_id": task["slots"][0]["slot_id"],
                "review_state": "confirmed",
                "source_status": "resolved",
                "position": 0,
            }
        ]
        saved = client.put(
            f"/v2/workspaces/{workspace_id}/tasks/{task['task_id']}",
            headers=headers,
            json={"expected_revision": task["revision"], "task": task},
        )
        assert saved.status_code == 200
        task = saved.json()
        stale = client.put(
            f"/v2/workspaces/{workspace_id}/tasks/{task['task_id']}",
            headers=headers,
            json={"expected_revision": 1, "task": task},
        )
        assert stale.status_code == 409
        markdown = client.get(
            f"/v2/workspaces/{workspace_id}/tasks/{task['task_id']}/markdown",
            headers=headers,
        )
        assert markdown.status_code == 200
        assert "第 1 页" in markdown.text
        assert "级数章节证据" in markdown.text

        authorization = client.get(
            f"/v2/workspaces/{workspace_id}/vision-authorization",
            headers=headers,
        )
        assert authorization.json()["vision_enabled"] is False
        enabled = client.put(
            f"/v2/workspaces/{workspace_id}/vision-authorization",
            headers=headers,
            json={"vision_enabled": True},
        )
        assert enabled.json()["vision_enabled"] is True

    assert {path.name: path.read_bytes() for path in raw.iterdir()} == before
