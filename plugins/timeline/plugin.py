from __future__ import annotations

import json
import os
from pathlib import Path


def cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


request = json.loads(Path(os.environ["OCTOPUS_PLUGIN_REQUEST"]).read_text(encoding="utf-8"))
response_path = Path(os.environ["OCTOPUS_PLUGIN_RESPONSE"])
signals = sorted(
    request.get("resources", {}).get("timeline_signals", []),
    key=lambda item: (str(item.get("modified_at", "")), str(item.get("name", ""))),
)
lines = [
    "# Octopus timeline",
    "",
    "| Modified | Name | Kind | Status | Node |",
    "| --- | --- | --- | --- | --- |",
]
for item in signals:
    lines.append(
        "| {modified_at} | {name} | {kind} | {status} | `{node_id}` |".format(
            **{key: cell(value) for key, value in item.items()}
        )
    )
response = {
    "summary": f"Exported {len(signals)} sanitized timeline signal(s).",
    "operations": [
        {
            "operation": "export_text",
            "path": "timeline.md",
            "content": "\n".join(lines) + "\n",
        }
    ],
}
response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
