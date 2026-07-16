from pathlib import Path

import pytest

from octopus.models import GeneratedSummary, GlobalWorkspace
from octopus.research_ai import (
    confirm_research_proposal,
    create_research_proposal,
    run_ai_index,
)
from octopus.workspace_v2 import WorkspaceStore


class FakeResearchProvider:
    def generate_leaf(self, document):
        return GeneratedSummary(
            one_sentence_summary=f"{document.name} 的学习资料。",
            description="可用于研究目标的本地资料。",
            tag_rough=["学习"],
            topic_keywords=["alpha"],
            recommended_reading=[],
        )

    def summarize_folder(self, name, children):
        return GeneratedSummary(
            one_sentence_summary=f"{name} 的资料集合。",
            description="包含可用于研究的资料。",
            tag_rough=["资料"],
            topic_keywords=["alpha"],
            recommended_reading=[],
        )

    def _json_call(self, purpose, prompt, payload):
        candidate = payload["candidates"][0]
        return {
            "title": "Alpha 研究资料包",
            "summary": "根据本地证据生成的研究提案。",
            "warnings": [],
            "gaps": ["需要补充反例"],
            "slots": [
                {
                    "name": "核心证据",
                    "description": "直接支持目标的材料。",
                    "required": True,
                    "candidate_ids": [candidate["candidate_id"]],
                    "rationales": {candidate["candidate_id"]: "与目标直接相关。"},
                }
            ],
        }


def _workspace(tmp_path: Path) -> GlobalWorkspace:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "alpha.txt").write_text("alpha evidence " * 8, encoding="utf-8")
    workspace = GlobalWorkspace(
        workspace_id="workspace-research",
        name="研究资料",
        raw_path=str(raw),
        storage_path=str(tmp_path / "storage"),
        ai_policy={"enabled": True},
    )
    WorkspaceStore(workspace).sync()
    return workspace


def test_ai_index_is_incremental_and_resumable(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    import octopus.research_ai as module

    monkeypatch.setattr(module, "get_workspace", lambda _: workspace)
    monkeypatch.setattr(module, "create_provider", lambda *args, **kwargs: FakeResearchProvider())

    first = run_ai_index(workspace.workspace_id, limit=1)
    assert first["completed"] == 1
    assert first["status"]["estimated_calls"] == 1

    second = run_ai_index(workspace.workspace_id, limit=20)
    assert second["errors"] == []
    assert second["status"]["estimated_calls"] == 0


def test_research_proposal_only_uses_server_candidates(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    import octopus.research_ai as module

    monkeypatch.setattr(module, "get_workspace", lambda _: workspace)
    monkeypatch.setattr(module, "create_provider", lambda *args, **kwargs: FakeResearchProvider())

    proposal = create_research_proposal(
        workspace.workspace_id,
        "alpha",
        template_id="literature_review",
    )
    assert proposal.workspace_id == workspace.workspace_id
    assert proposal.template_id == "literature_review"
    assert proposal.slots[0].candidate_ids
    assert proposal.slots[0].candidate_ids[0] in {item.candidate_id for item in proposal.candidates}

    task = confirm_research_proposal(workspace.workspace_id, proposal)
    assert task.template_id == "literature_review"
    assert task.items[0].review_state == "pending"
    assert task.items[0].source_status == "resolved"


def test_research_proposal_rejects_tampered_or_duplicate_candidates(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    import octopus.research_ai as module

    monkeypatch.setattr(module, "get_workspace", lambda _: workspace)
    monkeypatch.setattr(module, "create_provider", lambda *args, **kwargs: FakeResearchProvider())
    proposal = create_research_proposal(workspace.workspace_id, "alpha")

    tampered = proposal.model_copy(deep=True)
    tampered.candidates[0].excerpt = "forged evidence"
    with pytest.raises(ValueError, match="no longer matches"):
        confirm_research_proposal(workspace.workspace_id, tampered)

    duplicate = proposal.model_copy(deep=True)
    duplicate.slots[0].candidate_ids.append(duplicate.slots[0].candidate_ids[0])
    with pytest.raises(ValueError, match="more than once"):
        confirm_research_proposal(workspace.workspace_id, duplicate)

    wrong_workspace = proposal.model_copy(update={"workspace_id": "another-workspace"})
    with pytest.raises(ValueError, match="another workspace"):
        confirm_research_proposal(workspace.workspace_id, wrong_workspace)


def test_research_proposal_handles_malformed_structured_output(tmp_path, monkeypatch):
    workspace = _workspace(tmp_path)
    import octopus.research_ai as module

    provider = FakeResearchProvider()
    provider._json_call = lambda *args, **kwargs: {
        "slots": [{"candidate_ids": 1, "rationales": []}],
        "warnings": 1,
        "gaps": None,
    }
    monkeypatch.setattr(module, "get_workspace", lambda _: workspace)
    monkeypatch.setattr(module, "create_provider", lambda *args, **kwargs: provider)

    proposal = create_research_proposal(workspace.workspace_id, "alpha")

    assert proposal.slots[0].candidate_ids
    assert proposal.gaps == []
