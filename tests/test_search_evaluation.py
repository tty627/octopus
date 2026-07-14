from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from octopus.cli import app
from octopus.search_evaluation import (
    REQUIRED_SEARCH_CATEGORIES,
    default_search_evaluation_dataset_path,
    evaluate_search_dataset,
    load_search_evaluation_dataset,
    validate_search_evaluation_dataset,
)


def test_versioned_search_dataset_covers_required_scenarios() -> None:
    dataset = load_search_evaluation_dataset(default_search_evaluation_dataset_path())

    covered = {category for task in dataset.tasks for category in task.categories}
    assert covered >= REQUIRED_SEARCH_CATEGORIES
    assert any(document.stale for document in dataset.documents)
    assert len({Path(document.path).name for document in dataset.documents}) < len(
        dataset.documents
    )


def test_search_evaluation_enforces_metrics_and_explanation_contract(tmp_path: Path) -> None:
    dataset = load_search_evaluation_dataset(default_search_evaluation_dataset_path())
    report = evaluate_search_dataset(dataset, tmp_path / "evaluation")

    assert report.top5_accuracy >= 0.80
    assert report.mean_reciprocal_rank >= 0.65
    assert report.task_failure_count == 0
    assert report.mean_inspection_step_reduction >= 0.30
    assert report.explanation_contract_failures == 0
    assert report.meets_engineering_thresholds is True
    stale = next(item for item in report.tasks if "stale" in item.categories)
    assert stale.explanation_contract_pass is True
    serialized = report.model_dump_json()
    assert str(tmp_path) not in serialized


def test_search_dataset_validation_rejects_missing_category() -> None:
    dataset = load_search_evaluation_dataset(default_search_evaluation_dataset_path())
    invalid = dataset.model_copy(update={"required_categories": ["chinese"]})

    with pytest.raises(ValueError, match="omits required categories"):
        validate_search_evaluation_dataset(invalid)


def test_evaluate_search_cli_writes_machine_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    result = CliRunner().invoke(
        app,
        ["evaluate-search", "--output", str(output), "--enforce"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["dataset_version"] == "1.0.0"
    assert payload["meets_engineering_thresholds"] is True
