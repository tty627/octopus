from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MetricSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["lower", "higher"]
    absolute_limit: float


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    baseline: float
    candidate: float
    direction: Literal["lower", "higher"]
    regression_fraction: float = Field(ge=0)
    passes_regression_gate: bool
    passes_absolute_gate: bool
    regression_approved: bool = False

    @property
    def passed(self) -> bool:
        return (
            self.passes_regression_gate or self.regression_approved
        ) and self.passes_absolute_gate


class PerformanceGateReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_regression_fraction: float
    metrics: list[MetricResult]
    passed: bool


METRICS = {
    "transaction_800_p95_ms": MetricSpec(direction="lower", absolute_limit=5_000),
    "transaction_growth_ratio": MetricSpec(direction="lower", absolute_limit=2.5),
    "incremental_p95_ms": MetricSpec(direction="lower", absolute_limit=30_000),
    "search_top5_rate": MetricSpec(direction="higher", absolute_limit=0.8),
}


def _regression(baseline: float, candidate: float, direction: str) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else float("inf")
    if direction == "lower":
        return max(0.0, (candidate - baseline) / abs(baseline))
    return max(0.0, (baseline - candidate) / abs(baseline))


def evaluate_performance_gate(
    baseline: dict[str, float],
    candidate: dict[str, float],
    *,
    maximum_regression_fraction: float = 0.10,
    approved_regressions: set[str] | None = None,
) -> PerformanceGateReport:
    if not 0 <= maximum_regression_fraction <= 1:
        raise ValueError("maximum regression fraction must be between zero and one")
    approvals = approved_regressions or set()
    unknown_approvals = approvals - set(METRICS)
    if unknown_approvals:
        raise ValueError("unknown approved metrics: " + ", ".join(sorted(unknown_approvals)))
    missing = sorted((set(METRICS) - baseline.keys()) | (set(METRICS) - candidate.keys()))
    if missing:
        raise ValueError("missing performance metrics: " + ", ".join(missing))
    results: list[MetricResult] = []
    for name, spec in METRICS.items():
        baseline_value = float(baseline[name])
        candidate_value = float(candidate[name])
        regression = _regression(baseline_value, candidate_value, spec.direction)
        absolute = (
            candidate_value <= spec.absolute_limit
            if spec.direction == "lower"
            else candidate_value >= spec.absolute_limit
        )
        results.append(
            MetricResult(
                name=name,
                baseline=baseline_value,
                candidate=candidate_value,
                direction=spec.direction,
                regression_fraction=regression,
                passes_regression_gate=regression <= maximum_regression_fraction,
                passes_absolute_gate=absolute,
                regression_approved=name in approvals,
            )
        )
    return PerformanceGateReport(
        maximum_regression_fraction=maximum_regression_fraction,
        metrics=results,
        passed=all(item.passed for item in results),
    )


def _load_metrics(path: Path) -> dict[str, float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
        raise ValueError(f"performance file has no metrics object: {path}")
    return {str(key): float(value) for key, value in payload["metrics"].items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approve", action="append", default=[])
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    report = evaluate_performance_gate(
        _load_metrics(arguments.baseline),
        _load_metrics(arguments.candidate),
        approved_regressions=set(arguments.approve),
    )
    rendered = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if arguments.enforce and not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
