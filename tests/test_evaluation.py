from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.benchmark_retrieval import run_retrieval_benchmark
from octopus.evaluation import (
    RETRIEVAL_DATASET_VERSION,
    StudyRecord,
    load_retrieval_tasks,
    study_assignments,
    study_record_json,
    summarize_study,
)

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks" / "retrieval" / "v1" / "tasks.jsonl"
JUDGMENTS = ROOT / "benchmarks" / "retrieval" / "v1" / "judgments.jsonl"


def test_versioned_retrieval_dataset_is_balanced_and_passes_gate() -> None:
    tasks = load_retrieval_tasks(TASKS)
    assert len(tasks) == 60
    assert sum(task.language == "zh" for task in tasks) == 30
    assert sum(task.language == "en" for task in tasks) == 30
    assert {"same_name", "stale", "quality_risk"} <= {
        tag for task in tasks for tag in task.challenge_tags
    }

    result = run_retrieval_benchmark(TASKS, JUDGMENTS)
    assert result["dataset_version"] == RETRIEVAL_DATASET_VERSION
    assert result["task_count"] == 60
    assert result["hit_at_5"] >= 0.8
    assert result["passes_v05_gate"] is True


def test_study_assignment_is_balanced_and_records_are_private() -> None:
    tasks = load_retrieval_tasks(TASKS)
    participant_id, assignments = study_assignments(tasks[:12], "researcher-01")
    assert len(participant_id) == 16
    assert sum(condition == "explorer" for _, condition in assignments) == 6
    assert sum(condition == "octopus" for _, condition in assignments) == 6

    rendered = study_record_json(
        StudyRecord(
            session_id="session",
            participant_id=participant_id,
            task_id=assignments[0][0].task_id,
            condition=assignments[0][1],
            duration_ms=500,
            success=True,
        )
    )
    assert "query" not in rendered
    assert "target_path" not in rendered
    assert "source_uri" not in rendered


def test_study_summary_enforces_versions_privacy_and_gate(tmp_path: Path) -> None:
    records = []
    for number in range(20):
        records.extend(
            [
                StudyRecord(
                    session_id=f"session-{number}",
                    participant_id=f"participant-{number}",
                    task_id="explorer-task",
                    condition="explorer",
                    duration_ms=1_000,
                    success=True,
                ),
                StudyRecord(
                    session_id=f"session-{number}",
                    participant_id=f"participant-{number}",
                    task_id="octopus-task",
                    condition="octopus",
                    duration_ms=400,
                    success=True,
                ),
            ]
        )
    path = tmp_path / "study.jsonl"
    path.write_text("\n".join(study_record_json(item) for item in records), encoding="utf-8")
    summary = summarize_study(path)
    assert summary["participant_count"] == 20
    assert summary["median_time_reduction"] == pytest.approx(0.6)
    assert summary["passes_v05_gate"] is True

    unsafe = tmp_path / "unsafe.jsonl"
    payload = json.loads(study_record_json(records[0]))
    payload["query"] = "private query"
    unsafe.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        summarize_study(unsafe)
