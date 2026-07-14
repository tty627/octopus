from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_artifact_manifest(directory: Path, version: str) -> None:
    directory = directory.resolve()
    manifest_path = directory / "build-manifest.json"
    checksum_path = directory / "SHA256SUMS.txt"
    if manifest_path.exists() or checksum_path.exists():
        raise FileExistsError("artifact manifest or checksum file already exists")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest = {
        "schema_version": "1.0",
        "version": version,
        "git_commit": commit,
        "git_worktree_clean": not dirty,
        "release_build": False,
        "signatures_valid": False,
        "built_at_utc": datetime.now(UTC).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    artifacts = sorted(
        (path for path in directory.iterdir() if path.is_file() and path != checksum_path),
        key=lambda path: path.name,
    )
    checksum_path.write_text(
        "".join(f"{sha256(path)} *{path.name}\n" for path in artifacts),
        encoding="ascii",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version", required=True)
    arguments = parser.parse_args()
    write_artifact_manifest(arguments.directory, arguments.version)


if __name__ == "__main__":
    main()
