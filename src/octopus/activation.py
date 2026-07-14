from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import Field

from . import __version__
from .config import global_config_path
from .models import OctopusModel, utc_now
from .utils import atomic_write_json, load_json

ACTIVATION_EXPORT_SCHEMA_VERSION = "1.0"
FORBIDDEN_ACTIVATION_FIELDS = {
    "api_key",
    "content",
    "file_name",
    "index_path",
    "query",
    "raw_path",
    "source_uri",
}


class ActivationRecord(OctopusModel):
    session_id: str
    version: str = __version__
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    sample_mode: bool
    stages: dict[str, str] = Field(default_factory=dict)
    outcome: str = "started"
    error_code: str = ""
    file_count: int = 0


class ActivationExport(OctopusModel):
    schema_version: str = ACTIVATION_EXPORT_SCHEMA_VERSION
    product_version: str
    exported_at: str = Field(default_factory=utc_now)
    record_count: int
    records: list[ActivationRecord]


class ActivationSummary(OctopusModel):
    product_version: str
    session_count: int
    finished_count: int
    success_count: int
    success_rate: float
    within_ten_minutes_count: int
    within_ten_minutes_rate: float
    outcomes: dict[str, int]
    error_codes: dict[str, int]
    meets_v04_session_thresholds: bool


class ActivationSession:
    def __init__(self, *, sample_mode: bool) -> None:
        self.record = ActivationRecord(
            session_id=uuid.uuid4().hex,
            started_at=utc_now(),
            sample_mode=sample_mode,
        )
        self._write()

    @property
    def path(self) -> Path:
        return (
            global_config_path().parent
            / "onboarding-runs"
            / f"{self.record.session_id}.json"
        )

    def stage(self, name: str) -> None:
        self.record.stages[name] = utc_now()
        self._write()

    def finish(self, outcome: str, *, error_code: str = "", file_count: int = 0) -> None:
        self.record.finished_at = utc_now()
        self.record.duration_ms = max(
            0,
            int(
                (
                    datetime.fromisoformat(self.record.finished_at)
                    - datetime.fromisoformat(self.record.started_at)
                ).total_seconds()
                * 1000
            ),
        )
        self.record.outcome = outcome
        self.record.error_code = error_code
        self.record.file_count = file_count
        self._write()

    def _write(self) -> None:
        atomic_write_json(self.path, self.record.model_dump(mode="json"))


def activation_record_directory() -> Path:
    return global_config_path().parent / "onboarding-runs"


def _validate_record_payload(payload: object, source: Path) -> ActivationRecord:
    if not isinstance(payload, dict):
        raise ValueError(f"Activation record must be a JSON object: {source}")
    forbidden = FORBIDDEN_ACTIVATION_FIELDS & set(payload)
    if forbidden:
        raise ValueError(
            f"Activation record contains forbidden private fields {sorted(forbidden)}: {source}"
        )
    unexpected = set(payload) - set(ActivationRecord.model_fields)
    if unexpected:
        raise ValueError(f"Activation record contains unexpected fields {sorted(unexpected)}")
    return ActivationRecord.model_validate(payload)


def export_activation_records(
    output: Path,
    *,
    product_version: str = __version__,
    records_directory: Path | None = None,
) -> ActivationExport:
    directory = records_directory or activation_record_directory()
    records = [
        _validate_record_payload(load_json(path), path)
        for path in sorted(directory.glob("*.json"))
    ]
    records = [record for record in records if record.version == product_version]
    if not records:
        raise ValueError(f"No activation records found for Octopus {product_version}")
    session_ids = [record.session_id for record in records]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("Activation records contain duplicate session IDs")
    records.sort(key=lambda record: (record.started_at, record.session_id))
    exported = ActivationExport(
        product_version=product_version,
        record_count=len(records),
        records=records,
    )
    atomic_write_json(output, exported.model_dump(mode="json"))
    return exported


def load_activation_export(path: Path) -> ActivationExport:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Activation export must be a JSON object: {path}")
    forbidden = FORBIDDEN_ACTIVATION_FIELDS & set(payload)
    if forbidden:
        raise ValueError(
            f"Activation export contains forbidden private fields: {sorted(forbidden)}"
        )
    unexpected = set(payload) - set(ActivationExport.model_fields)
    if unexpected:
        raise ValueError(f"Activation export contains unexpected fields: {sorted(unexpected)}")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise ValueError(f"Activation export records must be an array: {path}")
    validated_payload = dict(payload)
    validated_payload["records"] = [
        _validate_record_payload(item, path) for item in raw_records
    ]
    exported = ActivationExport.model_validate(validated_payload)
    if exported.record_count != len(exported.records):
        raise ValueError("Activation export record_count does not match its records")
    if not exported.records:
        raise ValueError("Activation export is empty")
    if any(record.version != exported.product_version for record in exported.records):
        raise ValueError("Activation export mixes product versions")
    return exported


def summarize_activation_exports(paths: list[Path]) -> ActivationSummary:
    if not paths:
        raise ValueError("No activation exports were provided")
    exports = [load_activation_export(path) for path in paths]
    versions = {exported.product_version for exported in exports}
    if len(versions) != 1:
        raise ValueError("Activation summary cannot mix product versions")
    records = [record for exported in exports for record in exported.records]
    session_ids = [record.session_id for record in records]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("Activation exports contain duplicate session IDs")
    count = len(records)
    finished = sum(bool(record.finished_at) for record in records)
    successes = sum(record.outcome == "success" for record in records)
    within_ten_minutes = sum(
        record.outcome == "success" and record.duration_ms <= 600_000 for record in records
    )
    outcomes = Counter(record.outcome for record in records)
    error_codes = Counter(record.error_code for record in records if record.error_code)
    return ActivationSummary(
        product_version=next(iter(versions)),
        session_count=count,
        finished_count=finished,
        success_count=successes,
        success_rate=successes / count,
        within_ten_minutes_count=within_ten_minutes,
        within_ten_minutes_rate=within_ten_minutes / count,
        outcomes=dict(sorted(outcomes.items())),
        error_codes=dict(sorted(error_codes.items())),
        meets_v04_session_thresholds=(
            count >= 20 and successes / count >= 0.95 and within_ten_minutes / count >= 0.8
        ),
    )


def summarize_activation_export(path: Path) -> ActivationSummary:
    return summarize_activation_exports([path])
