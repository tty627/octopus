from __future__ import annotations

import argparse
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from octopus.transactions import IndexTransaction, mark_transaction_complete


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def measure_transaction(count: int) -> dict[str, float]:
    with tempfile.TemporaryDirectory(prefix="octopus-transaction-") as temporary:
        index = Path(temporary) / "index"
        (index / ".octopus").mkdir(parents=True)
        tracemalloc.start()
        started = time.perf_counter()
        transaction = IndexTransaction(index)
        for number in range(count):
            transaction.write_text(index / "nodes" / f"{number:06d}.md", "x" * 256)
        staged_at = time.perf_counter()
        transaction.commit(index / ".octopus" / "repository-state.json", "{}")
        committed_at = time.perf_counter()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        mark_transaction_complete(index, transaction.run_id)
    return {
        "stage_ms": (staged_at - started) * 1_000,
        "commit_ms": (committed_at - staged_at) * 1_000,
        "total_ms": (committed_at - started) * 1_000,
        "peak_mib": peak / (1024 * 1024),
    }


def run_benchmark(counts: list[int], repeats: int = 5, warmups: int = 1) -> dict[str, object]:
    results: dict[str, object] = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
        },
        "repeats": repeats,
        "warmups": warmups,
        "counts": {},
    }
    count_results: dict[str, object] = {}
    for count in counts:
        for _ in range(warmups):
            measure_transaction(count)
        measurements = [measure_transaction(count) for _ in range(repeats)]
        totals = [item["total_ms"] for item in measurements]
        count_results[str(count)] = {
            "p50_total_ms": statistics.median(totals),
            "p95_total_ms": _percentile(totals, 0.95),
            "p50_stage_ms": statistics.median(item["stage_ms"] for item in measurements),
            "p50_commit_ms": statistics.median(item["commit_ms"] for item in measurements),
            "max_peak_mib": max(item["peak_mib"] for item in measurements),
            "measurements": measurements,
        }
    results["counts"] = count_results
    if "400" in count_results and "800" in count_results:
        smaller = float(count_results["400"]["p50_total_ms"])  # type: ignore[index]
        larger = float(count_results["800"]["p50_total_ms"])  # type: ignore[index]
        larger_p95 = float(count_results["800"]["p95_total_ms"])  # type: ignore[index]
        results["growth_ratio_400_to_800"] = larger / smaller
        results["passes_v03_gate"] = larger_p95 <= 5_000 and larger / smaller <= 2.5
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", nargs="+", type=int, default=[400, 800])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    result = run_benchmark(arguments.counts, arguments.repeats, arguments.warmups)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if arguments.enforce and not result.get("passes_v03_gate", False):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
