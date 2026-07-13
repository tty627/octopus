from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path

from octopus.config import create_repository, repository_config_path
from octopus.engine import UpdateEngine
from octopus.utils import atomic_write_json


def run_incremental_benchmark(repeats: int = 5, warmups: int = 1) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="octopus-incremental-") as temporary:
        root = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root / "appdata")
        try:
            raw = root / "raw"
            index = root / "index"
            raw.mkdir()
            source = raw / "incremental.txt"
            source.write_text("initial", encoding="utf-8")
            config = create_repository(raw, index, "Incremental Benchmark")
            config.ai_policy.enabled = False
            config.stability.minimum_quiet_seconds = 0
            config.stability.required_stable_scan_count = 1
            atomic_write_json(
                repository_config_path(index),
                config.model_dump(mode="json", by_alias=True),
            )
            UpdateEngine(index).run(force_path="*")
            timings: list[float] = []
            for number in range(warmups + repeats):
                source.write_text(f"incremental {number}", encoding="utf-8")
                started = time.perf_counter()
                UpdateEngine(index).run(force_path="incremental.txt")
                elapsed = (time.perf_counter() - started) * 1_000
                if number >= warmups:
                    timings.append(elapsed)
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata
    ordered = sorted(timings)
    p95 = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.95))]
    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "repeats": repeats,
        "p50_ms": statistics.median(timings),
        "p95_ms": p95,
        "measurements_ms": timings,
        "passes_v03_gate": p95 <= 30_000,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    result = run_incremental_benchmark(arguments.repeats, arguments.warmups)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if arguments.enforce and not result["passes_v03_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
