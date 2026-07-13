from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from octopus.config import (
    create_repository,
    load_repository_config,
    load_repository_state,
    repository_config_path,
)
from octopus.engine import UpdateEngine
from octopus.evaluation import evaluate_retrieval, load_retrieval_tasks
from octopus.rendering import read_machine_header
from octopus.search import SearchIndex
from octopus.utils import atomic_write_json, atomic_write_text

from .generate_retrieval_dataset import materialize_retrieval_dataset

ROOT = Path(__file__).resolve().parent
DEFAULT_TASKS = ROOT / "retrieval" / "v1" / "tasks.jsonl"
DEFAULT_JUDGMENTS = ROOT / "retrieval" / "v1" / "judgments.jsonl"


def _apply_stale_scenarios(index: Path, tasks_path: Path) -> None:
    config = load_repository_config(index)
    state = load_repository_state(index, config)
    nodes_by_path = {node.raw_relative_path: node for node in state.nodes.values()}
    nodes_by_id = state.nodes
    changed: set[Path] = set()
    for task in load_retrieval_tasks(tasks_path):
        if "stale" not in task.challenge_tags:
            continue
        node = nodes_by_path.get(task.target_path)
        if node is None:
            continue
        relative = node.index_relative_path
        if not relative and node.parent_node_id in nodes_by_id:
            relative = nodes_by_id[node.parent_node_id].index_relative_path
        if not relative:
            continue
        path = index / Path(relative.replace("/", os.sep))
        header, body = read_machine_header(path)
        if header.get("schema", {}).get("index_type") == "leaf":
            header.setdefault("update_control", {})["index_status"] = "stale"
        else:
            children = header.get("children_summary_layer", {}).get("direct_children", [])
            for child in children:
                if child.get("child_id") == node.node_id:
                    child["index_status"] = "stale"
        atomic_write_text(
            path,
            json.dumps(header, ensure_ascii=False, indent=2) + "\n\n" + body,
        )
        changed.add(path)
    if changed:
        SearchIndex(index).rebuild()


def run_retrieval_benchmark(
    tasks_path: Path = DEFAULT_TASKS,
    judgments_path: Path = DEFAULT_JUDGMENTS,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="octopus-retrieval-") as temporary:
        root = Path(temporary)
        previous_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = str(root / "appdata")
        try:
            raw = root / "raw"
            index = root / "index"
            materialize_retrieval_dataset(tasks_path, raw)
            config = create_repository(raw, index, "Retrieval Evaluation", ai_enabled=False)
            config.stability.minimum_quiet_seconds = 0
            config.stability.required_stable_scan_count = 1
            atomic_write_json(
                repository_config_path(index),
                config.model_dump(mode="json", by_alias=True),
            )
            UpdateEngine(index).run(force_path="*")
            _apply_stale_scenarios(index, tasks_path)
            return evaluate_retrieval(index, tasks_path, judgments_path)
        finally:
            if previous_appdata is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = previous_appdata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--judgments", type=Path, default=DEFAULT_JUDGMENTS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--enforce", action="store_true")
    arguments = parser.parse_args()
    result = run_retrieval_benchmark(arguments.tasks, arguments.judgments)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if arguments.enforce and not result["passes_v05_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
