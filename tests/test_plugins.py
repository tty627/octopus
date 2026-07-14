from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octopus.engine import UpdateEngine
from octopus.plugin_sdk import (
    PLUGIN_API_VERSION,
    discover_plugins,
    reference_plugins_directory,
    run_plugin,
)
from octopus.search import SearchIndex


def _write_plugin(
    root: Path,
    source: str,
    *,
    plugin_id: str = "test.local.plugin",
    plugin_api: str = ">=1.0,<2.0",
    permissions: list[str] | None = None,
) -> Path:
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "plugin_id": plugin_id,
                "name": "Test plugin",
                "version": "1.0.0",
                "plugin_api": plugin_api,
                "entrypoint": "plugin.py",
                "description": "Test-only plugin.",
                "permissions": permissions or [],
            }
        ),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text(source, encoding="utf-8")
    return root


def _response_source(response: dict[str, Any]) -> str:
    return (
        "import json, os\n"
        "from pathlib import Path\n"
        f"response = {response!r}\n"
        "Path(os.environ['OCTOPUS_PLUGIN_RESPONSE']).write_text("
        "json.dumps(response), encoding='utf-8')\n"
    )


def test_reference_plugins_are_discoverable_and_compatible() -> None:
    discovered = discover_plugins(reference_plugins_directory())

    assert {item["plugin_id"] for item in discovered} == {
        "octopus.reference.package",
        "octopus.reference.timeline",
    }
    assert all(item["compatible"] for item in discovered)
    assert PLUGIN_API_VERSION == "1.0"


def test_compatibility_and_grants_are_checked_before_process_creation(
    repository: tuple[Path, Path, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, index, _ = repository
    incompatible = _write_plugin(
        tmp_path / "incompatible",
        "raise AssertionError('must not execute')\n",
        plugin_api=">=2.0",
    )

    def unexpected_process(*args: object, **kwargs: object) -> None:
        raise AssertionError("plugin process must not start")

    monkeypatch.setattr("octopus.plugin_sdk.subprocess.run", unexpected_process)
    with pytest.raises(PermissionError, match="incompatible_plugin_api"):
        run_plugin(incompatible, index, tmp_path / "out-a", granted_permissions=set())
    with pytest.raises(PermissionError, match="permission_not_granted"):
        run_plugin(
            reference_plugins_directory() / "package",
            index,
            tmp_path / "out-b",
            granted_permissions={"index.query"},
            query="anything",
        )


def test_package_plugin_copies_only_confirmed_results_without_mutating_raw(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    raw, index, _ = repository
    original = "Atlas 项目预算证据\n".encode()
    source = raw / "项目预算.txt"
    source.write_bytes(original)
    UpdateEngine(index).run(force_path="*")
    target = next(
        item for item in SearchIndex(index).search("Atlas 项目预算") if item.name == source.name
    )

    export = tmp_path / "package-output"
    report = run_plugin(
        reference_plugins_directory() / "package",
        index,
        export,
        granted_permissions={"index.query", "export.write", "export.copy_confirmed"},
        query="Atlas 项目预算",
        confirmed_node_ids={target.node_id},
    )

    manifest = json.loads((export / "package-manifest.json").read_text(encoding="utf-8"))
    copied_path = export / manifest["files"][0]["export_path"]
    assert copied_path.read_bytes() == original
    assert report.copied_node_ids == [target.node_id]
    assert set(report.exported_files) == {
        "package-manifest.json",
        manifest["files"][0]["export_path"],
    }
    assert source.read_bytes() == original


def test_rejected_copy_plan_leaves_the_export_directory_empty(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    raw, index, _ = repository
    (raw / "private.txt").write_text("private Atlas material", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    target = next(
        item for item in SearchIndex(index).search("private Atlas") if item.name == "private.txt"
    )
    plugin = _write_plugin(
        tmp_path / "unconfirmed-copy",
        _response_source(
            {
                "summary": "attempt copy",
                "operations": [
                    {
                        "operation": "export_text",
                        "path": "partial.txt",
                        "content": "must not remain",
                    },
                    {
                        "operation": "copy_source",
                        "path": "stolen.txt",
                        "node_id": target.node_id,
                    },
                ],
            }
        ),
        permissions=["index.query", "export.write", "export.copy_confirmed"],
    )
    export = tmp_path / "rejected-output"

    with pytest.raises(PermissionError, match="unconfirmed"):
        run_plugin(
            plugin,
            index,
            export,
            granted_permissions={"index.query", "export.write", "export.copy_confirmed"},
            query="private Atlas",
        )

    assert list(export.iterdir()) == []


def test_timeline_plugin_receives_only_path_sanitized_signals(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    raw, index, _ = repository
    source = raw / "内部" / "里程碑.txt"
    source.parent.mkdir()
    source.write_text("timeline milestone", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")

    export = tmp_path / "timeline-output"
    report = run_plugin(
        reference_plugins_directory() / "timeline",
        index,
        export,
        granted_permissions={"index.timeline", "export.write"},
    )
    timeline = (export / "timeline.md").read_text(encoding="utf-8")

    assert report.exported_files == ["timeline.md"]
    assert "里程碑.txt" in timeline
    assert str(raw.resolve()) not in timeline
    assert str(index.resolve()) not in timeline
    assert "内部/里程碑.txt" not in timeline


@pytest.mark.parametrize("attack", ["raw_read", "network"])
def test_worker_blocks_raw_reads_and_network_without_damaging_repository(
    repository: tuple[Path, Path, object], tmp_path: Path, attack: str
) -> None:
    raw, index, _ = repository
    source = raw / "protected.txt"
    source.write_text("immutable protected material", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    before = source.read_bytes()
    if attack == "raw_read":
        code = (
            "from pathlib import Path\n"
            f"Path({str(source.resolve())!r}).read_bytes()\n"
        )
    else:
        code = "import socket\nsocket.create_connection(('127.0.0.1', 9), timeout=0.1)\n"
    plugin = _write_plugin(
        tmp_path / attack, code, plugin_id=f"test.local.{attack.replace('_', '-')}"
    )

    with pytest.raises(RuntimeError, match="plugin_failed"):
        run_plugin(plugin, index, tmp_path / f"{attack}-output", granted_permissions=set())

    assert source.read_bytes() == before
    assert SearchIndex(index).search("protected material")


def test_worker_strips_parent_secrets_and_failure_logs_hide_paths(
    repository: tuple[Path, Path, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, index, _ = repository
    monkeypatch.setenv("DEEPSEEK_API_KEY", "super-secret")
    environment_probe = _write_plugin(
        tmp_path / "environment-probe",
        (
            "import json, os\n"
            "from pathlib import Path\n"
            "present = 'DEEPSEEK_API_KEY' in os.environ\n"
            "response = {'operations': [{'operation': 'export_text', 'path': 'probe.txt', "
            "'content': str(present).lower()}]}\n"
            "Path(os.environ['OCTOPUS_PLUGIN_RESPONSE']).write_text("
            "json.dumps(response), encoding='utf-8')\n"
        ),
        permissions=["export.write"],
    )
    export = tmp_path / "environment-output"
    run_plugin(
        environment_probe,
        index,
        export,
        granted_permissions={"export.write"},
    )
    assert (export / "probe.txt").read_text(encoding="utf-8") == "false"

    failure = _write_plugin(
        tmp_path / "failure-log",
        f"print({str(raw.resolve())!r})\nprint('token=super-secret')\nraise RuntimeError('boom')\n",
        plugin_id="test.local.failure-log",
    )
    with pytest.raises(RuntimeError) as captured:
        run_plugin(failure, index, tmp_path / "failure-output", granted_permissions=set())
    message = str(captured.value)
    assert str(raw.resolve()) not in message
    assert "super-secret" not in message
    assert "<redacted>" in message
