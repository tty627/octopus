from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from octopus import __version__
from octopus.activation import ActivationSession
from octopus.cli import app
from octopus.engine import UpdateEngine
from octopus.providers import HeuristicProvider
from octopus.upgrade import UpgradeCheckResult, UpgradeStatus

runner = CliRunner()


def test_upgrade_check_supports_table_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    result = UpgradeCheckResult(
        status=UpgradeStatus.update_available,
        latest_version="0.4.0",
        release_url="https://github.com/tty627/octopus/releases/tag/v0.4.0",
        release_notes="Windows release",
        checked_at="2026-07-13T00:00:00+00:00",
    )
    monkeypatch.setattr("octopus.cli.check_for_upgrade", lambda force: result)

    table = runner.invoke(app, ["upgrade", "check", "--format", "table"])
    payload = runner.invoke(app, ["upgrade", "check", "--format", "json"])
    invalid = runner.invoke(app, ["upgrade", "check", "--format", "xml"])

    assert table.exit_code == 0
    assert "Windows release" in table.stdout
    assert payload.exit_code == 0
    assert '"status": "update_available"' in payload.stdout
    assert invalid.exit_code == 2


def test_acceptance_records_require_explicit_local_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    session = ActivationSession(sample_mode=True)
    session.stage("opened_result")
    session.finish("success", file_count=6)
    exported = tmp_path / "candidate.json"

    export_result = runner.invoke(app, ["acceptance", "export", "--output", str(exported)])
    summary_result = runner.invoke(
        app,
        [
            "acceptance",
            "summarize",
            "--records",
            str(tmp_path),
            "--output",
            str(tmp_path / "summary.json"),
        ],
    )

    assert export_result.exit_code == 0, export_result.stdout
    assert exported.exists()
    assert '"record_count": 1' in export_result.stdout
    assert summary_result.exit_code == 0, summary_result.stdout
    assert '"success_count": 1' in summary_result.stdout
    assert (tmp_path / "summary.json").exists()


def test_cli_version_and_repository_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    raw = tmp_path / "raw"
    index = tmp_path / "index"
    raw.mkdir()

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert __version__ in version.stdout

    initialized = runner.invoke(
        app,
        [
            "init",
            "--raw",
            str(raw),
            "--index",
            str(index),
            "--name",
            "CLI Repository",
            "--no-build",
        ],
    )
    assert initialized.exit_code == 0, initialized.stdout
    assert "Initialized" in initialized.stdout

    listed = runner.invoke(app, ["repo", "list"])
    assert listed.exit_code == 0
    assert "CLI Repository" in listed.stdout

    shown = runner.invoke(app, ["repo", "show"])
    assert shown.exit_code == 0
    assert "CLI Repository" in shown.stdout

    doctor = runner.invoke(app, ["doctor", "--format", "json"])
    assert doctor.exit_code == 0
    assert '"check": "Python 3.12+"' in doctor.stdout

    (raw / "notes.txt").write_text("Octopus 诊断测试", encoding="utf-8")
    dry_run = runner.invoke(
        app,
        ["update", "--repository", str(index), "--dry-run", "--format", "json"],
    )
    assert dry_run.exit_code == 0, dry_run.stdout
    assert '"text_updates"' in dry_run.stdout

    UpdateEngine(index).run(force_path="*")
    validated = runner.invoke(app, ["validate", "--repository", str(index), "--format", "json"])
    assert validated.exit_code == 0, validated.stdout
    last_report = runner.invoke(
        app, ["report", "--repository", str(index), "--last", "--format", "json"]
    )
    assert last_report.exit_code == 0
    assert f'"version": "{__version__}"' in last_report.stdout

    normal_search = runner.invoke(
        app,
        ["search", "CLI Repository", "--repository", str(index), "--format", "json"],
    )
    assert normal_search.exit_code == 0
    assert normal_search.stdout.lstrip().startswith("[")

    monkeypatch.setattr(
        "octopus.search.create_provider", lambda config, require_network: HeuristicProvider()
    )
    full_search = runner.invoke(
        app,
        [
            "search",
            "CLI Repository",
            "--repository",
            str(index),
            "--full",
            "--format",
            "report-json",
        ],
    )
    assert full_search.exit_code == 0, full_search.stdout
    assert '"answer"' in full_search.stdout
    assert '"results"' in full_search.stdout
    assert '"citations"' in full_search.stdout
