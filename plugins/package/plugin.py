from __future__ import annotations

import json
import os
import re
from pathlib import Path


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w. -]+", "_", Path(value).name, flags=re.UNICODE).strip(" .")
    return cleaned[:120] or "source"


request = json.loads(Path(os.environ["OCTOPUS_PLUGIN_REQUEST"]).read_text(encoding="utf-8"))
response_path = Path(os.environ["OCTOPUS_PLUGIN_RESPONSE"])
resources = request.get("resources", {})
confirmed = set(resources.get("confirmed_node_ids", []))
selected = [
    item for item in resources.get("search_results", []) if item.get("node_id") in confirmed
]

files = []
operations = []
for item in selected:
    node_id = str(item["node_id"])
    export_path = f"files/{node_id[:12]}-{safe_name(str(item.get('name', 'source')))}"
    files.append(
        {
            "node_id": node_id,
            "name": item.get("name", ""),
            "index_type": item.get("index_type", ""),
            "export_path": export_path,
        }
    )
    operations.append({"operation": "copy_source", "path": export_path, "node_id": node_id})

manifest = {
    "schema_version": "1.0",
    "invocation_id": request["invocation_id"],
    "plugin_id": request["plugin"]["plugin_id"],
    "files": files,
}
operations.append(
    {
        "operation": "export_text",
        "path": "package-manifest.json",
        "content": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }
)
response = {
    "summary": f"Prepared {len(files)} explicitly confirmed source file(s).",
    "operations": operations,
}
response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
