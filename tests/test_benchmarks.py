from __future__ import annotations

from pathlib import Path

from benchmarks.benchmark_incremental import run_incremental_benchmark
from benchmarks.benchmark_transactions import run_benchmark
from benchmarks.generate_dataset import generate_dataset
from benchmarks.performance_gate import evaluate_performance_gate


def test_deterministic_mixed_dataset_generator(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    manifest = generate_dataset(root, 14, "mixed")
    assert manifest["count"] == 14
    assert len(manifest["files"]) == 14  # type: ignore[arg-type]
    assert (root / "dataset-manifest.json").exists()
    assert not (root / ".templates").exists()
    assert any(root.rglob("*.docx"))
    assert any(root.rglob("*.png"))


def test_small_transaction_and_incremental_benchmark_smoke() -> None:
    transaction = run_benchmark([5], repeats=1, warmups=0)
    assert "5" in transaction["counts"]  # type: ignore[operator]
    incremental = run_incremental_benchmark(repeats=1, warmups=0)
    assert incremental["passes_v03_gate"] is True


def test_performance_gate_blocks_absolute_or_unapproved_regressions() -> None:
    baseline = {
        "transaction_800_p95_ms": 2_500,
        "transaction_growth_ratio": 1.2,
        "incremental_p95_ms": 100,
        "search_top5_rate": 0.9,
    }
    passing = evaluate_performance_gate(
        baseline,
        {
            "transaction_800_p95_ms": 2_600,
            "transaction_growth_ratio": 1.25,
            "incremental_p95_ms": 105,
            "search_top5_rate": 0.89,
        },
    )
    regressed = evaluate_performance_gate(
        baseline,
        {
            "transaction_800_p95_ms": 2_800,
            "transaction_growth_ratio": 1.4,
            "incremental_p95_ms": 31_000,
            "search_top5_rate": 0.79,
        },
    )

    assert passing.passed
    assert not regressed.passed
    assert {item.name for item in regressed.metrics if not item.passed} == {
        "transaction_800_p95_ms",
        "transaction_growth_ratio",
        "incremental_p95_ms",
        "search_top5_rate",
    }

    approved = evaluate_performance_gate(
        baseline,
        {
            "transaction_800_p95_ms": 2_800,
            "transaction_growth_ratio": 1.4,
            "incremental_p95_ms": 100,
            "search_top5_rate": 0.9,
        },
        approved_regressions={"transaction_800_p95_ms", "transaction_growth_ratio"},
    )
    assert approved.passed
    assert {
        item.name for item in approved.metrics if item.regression_approved
    } == {"transaction_800_p95_ms", "transaction_growth_ratio"}
