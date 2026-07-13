from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import tempfile
import time
from pathlib import Path

from octopus.onboarding import ESTIMATE_COEFFICIENT_VERSION
from octopus.parsers import ParserRegistry
from octopus.sample_data import materialize_sample_repository


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile_value) - 1)
    return ordered[index]


def benchmark(repeats: int) -> dict[str, object]:
    registry = ParserRegistry()
    profiles: dict[str, dict[str, float]] = {}
    with tempfile.TemporaryDirectory(prefix="octopus-estimate-") as temporary:
        sample = materialize_sample_repository(Path(temporary) / "sample")
        for source in sorted(sample.iterdir(), key=lambda path: path.suffix):
            registry.extract(source)  # Warm model imports and parser caches.
            timings: list[float] = []
            extracted_bytes = 0
            for _ in range(repeats):
                started = time.perf_counter()
                document = registry.extract(source)
                timings.append(time.perf_counter() - started)
                extracted_bytes = len(document.text.encode("utf-8"))
            profiles[source.suffix.casefold()] = {
                "seconds_p50": round(statistics.median(timings), 4),
                "seconds_p95": round(percentile(timings, 0.95), 4),
                "extracted_to_source_ratio": round(
                    extracted_bytes / max(1, source.stat().st_size), 4
                ),
            }
    return {
        "coefficient_version": ESTIMATE_COEFFICIENT_VERSION,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "repeats": repeats,
        "profiles": profiles,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate v0.4 Windows preflight estimates")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.repeats < 3:
        parser.error("--repeats must be at least 3")
    result = benchmark(arguments.repeats)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
