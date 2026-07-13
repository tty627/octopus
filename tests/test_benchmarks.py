from __future__ import annotations

from pathlib import Path

from benchmarks.benchmark_incremental import run_incremental_benchmark
from benchmarks.benchmark_transactions import run_benchmark
from benchmarks.generate_dataset import generate_dataset


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
