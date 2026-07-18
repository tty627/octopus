from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from octopus import __version__
from octopus.release_audit import (
    _open_release_blockers,
    audit_release,
    runtime_contracts,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_directory(root: Path) -> Path:
    root.mkdir()
    (root / f"octopus_index-{__version__}-py3-none-any.whl").write_bytes(b"wheel")
    (root / f"octopus_index-{__version__}.tar.gz").write_bytes(b"sdist")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    (root / "build-manifest.json").write_text(
        json.dumps(
            {
                "version": __version__,
                "git_commit": commit,
                "git_worktree_clean": True,
                "release_build": False,
            }
        ),
        encoding="utf-8",
    )
    artifacts = sorted(root.iterdir(), key=lambda path: path.name)
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{_sha256(path)} *{path.name}\n" for path in artifacts),
        encoding="ascii",
    )
    return root


def test_repository_release_audit_freezes_contracts_and_has_no_blockers() -> None:
    report = audit_release(ROOT, __version__)

    assert report.engineering_passed
    assert all(check.status == "pass" for check in report.checks)
    assert runtime_contracts() == json.loads(
        (ROOT / "docs" / "product" / "contract-freeze-v1.json").read_text(encoding="utf-8")
    )["contracts"]
    assert report.external_gates_not_executed
    assert (ROOT / "docs" / "releases" / f"v{__version__}.md").is_file()


def test_artifact_audit_verifies_versions_manifest_and_every_checksum(tmp_path: Path) -> None:
    artifacts = _artifact_directory(tmp_path / "artifacts")

    passing = audit_release(ROOT, __version__, artifact_directory=artifacts)
    assert passing.engineering_passed
    assert {item.check_id for item in passing.checks if item.status == "fail"} == set()

    wheel = next(artifacts.glob("*.whl"))
    wheel.write_bytes(b"corrupted after checksums")
    failed = audit_release(ROOT, __version__, artifact_directory=artifacts)
    assert not failed.engineering_passed
    assert next(item for item in failed.checks if item.check_id == "artifact_checksums").status == (
        "fail"
    )


def test_open_p0_or_p1_is_a_release_blocker() -> None:
    assert _open_release_blockers(
        {
            "issues": [
                {"id": "P0-open", "severity": "P0", "status": "open"},
                {"id": "P1-fixed", "severity": "P1", "status": "resolved"},
                {"id": "P2-open", "severity": "P2", "status": "open"},
            ]
        }
    ) == ["P0-open"]
