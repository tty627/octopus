from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from . import __version__
from .models import OctopusModel, utc_now
from .search import SEARCH_ALGORITHM_VERSION, SearchIndex

RETRIEVAL_DATASET_VERSION = "octopus-retrieval-v1"
STUDY_RECORD_SCHEMA_VERSION = "1.0"
FORBIDDEN_STUDY_FIELDS = {"query", "path", "raw_path", "source_uri", "content"}


class RetrievalTask(OctopusModel):
    task_id: str
    language: Literal["zh", "en"]
    query: str
    target_path: str
    format: str
    challenge_tags: list[str] = Field(default_factory=list)
    title: str = ""
    content: str = ""


class StudyRecord(OctopusModel):
    schema_version: str = STUDY_RECORD_SCHEMA_VERSION
    session_id: str
    participant_id: str
    product_version: str = __version__
    dataset_version: str = RETRIEVAL_DATASET_VERSION
    task_id: str
    condition: Literal["explorer", "octopus"]
    duration_ms: int = Field(ge=0)
    success: bool
    error_code: str = ""
    recorded_at: str = Field(default_factory=utc_now)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row {number} must be an object: {path}")
        values.append(value)
    return values


def load_retrieval_tasks(path: Path) -> list[RetrievalTask]:
    tasks = [RetrievalTask.model_validate(item) for item in load_jsonl(path)]
    identifiers = [task.task_id for task in tasks]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Retrieval task IDs must be unique")
    return tasks


def load_judgments(path: Path) -> dict[str, str]:
    judgments: dict[str, str] = {}
    for item in load_jsonl(path):
        task_id = str(item.get("task_id", ""))
        target_path = str(item.get("target_path", ""))
        if not task_id or not target_path or task_id in judgments:
            raise ValueError("Judgments require unique task_id and target_path values")
        judgments[task_id] = target_path.replace("\\", "/")
    return judgments


def evaluate_retrieval(
    index_repository: Path,
    tasks_path: Path,
    judgments_path: Path,
    *,
    dataset_version: str = RETRIEVAL_DATASET_VERSION,
) -> dict[str, Any]:
    tasks = load_retrieval_tasks(tasks_path)
    judgments = load_judgments(judgments_path)
    if {task.task_id for task in tasks} != set(judgments):
        raise ValueError("Task and judgment IDs must match exactly")
    search = SearchIndex(index_repository)
    results: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    hits_at_1 = 0
    hits_at_5 = 0
    for task in tasks:
        report = search.search_report(task.query, limit=5, mode="local")
        target = judgments[task.task_id].casefold()
        rank = next(
            (
                item.rank
                for item in report.results
                if item.raw_relative_path.replace("\\", "/").casefold() == target
            ),
            0,
        )
        hits_at_1 += int(rank == 1)
        hits_at_5 += int(1 <= rank <= 5)
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        results.append(
            {
                "task_id": task.task_id,
                "language": task.language,
                "format": task.format,
                "challenge_tags": task.challenge_tags,
                "target_rank": rank,
                "hit_at_5": bool(rank),
                "failure_reason": "" if rank else "target_not_in_top_5",
            }
        )
    count = len(tasks)
    hit_at_1 = hits_at_1 / count if count else 0.0
    hit_at_5 = hits_at_5 / count if count else 0.0
    return {
        "dataset_version": dataset_version,
        "algorithm_version": SEARCH_ALGORITHM_VERSION,
        "product_version": __version__,
        "task_count": count,
        "hit_at_1": hit_at_1,
        "hit_at_5": hit_at_5,
        "mrr": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0,
        "passes_v05_gate": hit_at_5 >= 0.8,
        "tasks": results,
    }


def study_assignments(
    tasks: list[RetrievalTask], participant: str
) -> tuple[str, list[tuple[RetrievalTask, Literal["explorer", "octopus"]]]]:
    participant_id = (
        hashlib.sha256(participant.encode("utf-8")).hexdigest()[:16]
        if participant
        else uuid.uuid4().hex[:16]
    )
    seed = int(hashlib.sha256(participant_id.encode("ascii")).hexdigest()[:8], 16)
    ordered = sorted(
        tasks,
        key=lambda task: hashlib.sha256(
            f"{participant_id}:{task.task_id}".encode()
        ).hexdigest(),
    )
    assignments: list[tuple[RetrievalTask, Literal["explorer", "octopus"]]] = []
    for index, task in enumerate(ordered):
        condition: Literal["explorer", "octopus"] = (
            "explorer" if (index + seed) % 2 == 0 else "octopus"
        )
        assignments.append((task, condition))
    return participant_id, assignments


def study_record_json(record: StudyRecord) -> str:
    payload = record.model_dump(mode="json")
    if FORBIDDEN_STUDY_FIELDS & set(payload):
        raise ValueError("Study records must not contain query, path, URI, or content")
    return json.dumps(payload, ensure_ascii=False)


def _percentile(values: list[int], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, round((len(ordered) - 1) * ratio))])


def summarize_study(records_path: Path) -> dict[str, Any]:
    raw_records = load_jsonl(records_path)
    for item in raw_records:
        if FORBIDDEN_STUDY_FIELDS & set(item):
            raise ValueError("Study records contain forbidden query/path/content fields")
    records = [StudyRecord.model_validate(item) for item in raw_records]
    if not records:
        raise ValueError("Study record file is empty")
    versions = {(item.product_version, item.dataset_version) for item in records}
    if len(versions) != 1:
        raise ValueError("Study aggregation cannot mix product or dataset versions")
    keys = [(item.session_id, item.task_id, item.condition) for item in records]
    if len(keys) != len(set(keys)):
        raise ValueError("Study records contain duplicate session/task/condition rows")
    conditions: dict[str, dict[str, Any]] = {}
    for condition in ("explorer", "octopus"):
        selected = [item for item in records if item.condition == condition]
        successful = [item.duration_ms for item in selected if item.success]
        conditions[condition] = {
            "attempts": len(selected),
            "successes": len(successful),
            "success_rate": len(successful) / len(selected) if selected else 0.0,
            "p50_ms": statistics.median(successful) if successful else 0.0,
            "p95_ms": _percentile(successful, 0.95),
        }
    explorer = float(conditions["explorer"]["p50_ms"])
    octopus = float(conditions["octopus"]["p50_ms"])
    improvement = (explorer - octopus) / explorer if explorer > 0 else 0.0
    participants = {item.participant_id for item in records}
    product_version, dataset_version = next(iter(versions))
    return {
        "product_version": product_version,
        "dataset_version": dataset_version,
        "participant_count": len(participants),
        "conditions": conditions,
        "median_time_reduction": improvement,
        "passes_v05_gate": len(participants) >= 20 and improvement >= 0.5,
    }
