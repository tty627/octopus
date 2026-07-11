from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import utc_now


class UpdateLogger:
    def __init__(self, octopus_directory: Path) -> None:
        self.directory = octopus_directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.events_path = self.directory / "update-events.jsonl"
        self.markdown_path = self.directory / "update-log.md"

    def event(
        self,
        event_type: str,
        node_id: str = "",
        state_before: str = "",
        state_after: str = "",
        message: str = "",
        error: str = "",
        **extra: Any,
    ) -> None:
        payload = {
            "timestamp": utc_now(),
            "event_type": event_type,
            "node_id": node_id,
            "state_before": state_before,
            "state_after": state_after,
            "message": message,
            "error": error,
            **extra,
        }
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def run_summary(self, stats: dict[str, Any]) -> None:
        if not self.markdown_path.exists():
            self.markdown_path.write_text("# Octopus 更新日志\n\n", encoding="utf-8")
        lines = [f"## {utc_now()}", ""]
        lines.extend(f"- {key}: {value}" for key, value in stats.items())
        lines.append("")
        with self.markdown_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
