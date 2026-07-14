from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from octopus.runtime import octopus_command
from octopus.upgrade import UpgradeStatus, check_for_upgrade, upgrade_cache_path


def test_upgrade_check_validates_release_and_uses_daily_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    calls = 0

    def release(timeout: float) -> object:
        nonlocal calls
        calls += 1
        assert timeout == 3.0
        return {
            "tag_name": "v0.6.0",
            "html_url": "https://github.com/tty627/octopus/releases/tag/v0.6.0",
            "body": "Windows offline installer and onboarding wizard.",
        }

    monkeypatch.setattr("octopus.upgrade._fetch_release", release)
    now = datetime(2026, 7, 13, tzinfo=UTC)
    fresh = check_for_upgrade(force=True, now=now)
    cached = check_for_upgrade(now=now + timedelta(hours=2))

    assert fresh.status == UpgradeStatus.update_available
    assert fresh.release_notes == "Windows offline installer and onboarding wizard."
    assert not fresh.cached
    assert cached.cached
    assert calls == 1
    assert upgrade_cache_path().exists()


def test_upgrade_check_rejects_untrusted_release_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        "octopus.upgrade._fetch_release",
        lambda timeout: {
            "tag_name": "v9.0.0",
            "html_url": "https://example.invalid/octopus.exe",
        },
    )

    result = check_for_upgrade(force=True)

    assert result.status == UpgradeStatus.unavailable
    assert result.error_code == "invalid_response"
    assert not result.release_url


def test_runtime_command_handles_source_and_frozen_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert octopus_command("version") == [sys.executable, "-m", "octopus", "version"]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Octopus\octopus.exe")
    assert octopus_command("version") == [
        r"C:\Program Files\Octopus\octopus.exe",
        "version",
    ]
