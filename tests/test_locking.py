from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octopus.locking import RepositoryBusyError, RepositoryLock


def test_repository_lock_blocks_live_process_and_cleans_up(tmp_path: Path) -> None:
    path = tmp_path / "update.lock"
    with RepositoryLock(path, "update", "raw-id", tmp_path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        with (
            pytest.raises(RepositoryBusyError, match="locked by PID"),
            RepositoryLock(path, "scan", "raw-id", tmp_path),
        ):
            pass
    assert not path.exists()


def test_repository_lock_recovers_stale_record(tmp_path: Path) -> None:
    path = tmp_path / "update.lock"
    path.write_text(json.dumps({"pid": 999_999_999, "operation": "update"}), encoding="utf-8")
    with RepositoryLock(path, "update", "raw-id", tmp_path):
        assert path.exists()
    assert not path.exists()
    assert (tmp_path / "update.lock.stale-999999999").exists()
