from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from octopus.cli import app

runner = CliRunner()


def test_cli_version_and_repository_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    raw = tmp_path / "raw"
    index = tmp_path / "index"
    raw.mkdir()

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert "0.1.0" in version.stdout

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
