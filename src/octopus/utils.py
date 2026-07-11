from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def quick_hash_file(path: Path, sample_size: int = 64 * 1024) -> str:
    stat = path.stat()
    digest = hashlib.blake2b(digest_size=20)
    digest.update(str(stat.st_size).encode())
    digest.update(str(stat.st_mtime_ns).encode())
    with path.open("rb") as stream:
        digest.update(stream.read(sample_size))
        if stat.st_size > sample_size * 2:
            stream.seek(max(sample_size, stat.st_size // 2))
            digest.update(stream.read(sample_size))
        if stat.st_size > sample_size:
            stream.seek(max(0, stat.st_size - sample_size))
            digest.update(stream.read(sample_size))
    return digest.hexdigest()


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_slug(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" .")
    return (cleaned or "unnamed")[:limit]


def truncate(text: str, limit: int = 80_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[Octopus: content truncated]"
