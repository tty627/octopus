from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True


def ensure_outside_raw(target: Path, raw_root: Path) -> None:
    target = target.resolve()
    raw_root = raw_root.resolve()
    if target == raw_root or raw_root in target.parents:
        raise PermissionError(f"Refusing to write inside Raw Repository: {target}")


@dataclass(frozen=True)
class RawRepository:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())

    def resolve_relative(self, relative_path: str) -> Path:
        candidate = (self.root / Path(relative_path.replace("/", os.sep))).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError(f"Path escapes Raw Repository: {relative_path}")
        return candidate

    def read_bytes(self, relative_path: str) -> bytes:
        return self.resolve_relative(relative_path).read_bytes()

    def read_text(self, relative_path: str, limit: int = 80_000) -> str:
        path = self.resolve_relative(relative_path)
        with path.open("r", encoding="utf-8-sig", errors="replace") as stream:
            return stream.read(limit)

    def stat(self, relative_path: str) -> os.stat_result:
        return self.resolve_relative(relative_path).stat()
