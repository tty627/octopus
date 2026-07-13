from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from pydantic import Field

from . import __version__
from .config import global_config_path
from .models import OctopusModel, utc_now
from .utils import atomic_write_json


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
