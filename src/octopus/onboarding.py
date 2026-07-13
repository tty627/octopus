from __future__ import annotations

import fnmatch
import os
import shutil
from collections import Counter
from enum import StrEnum
from pathlib import Path

from .models import RepositoryEstimate
from .parsers import IMAGE_EXTENSIONS, TEXT_EXTENSIONS
from .scanner import DEFAULT_IGNORED_DIRECTORIES, DEFAULT_IGNORED_GLOBS

ESTIMATE_COEFFICIENT_VERSION = "win11-x64-2026-07-v1"
MIB = 1024 * 1024


class OnboardingErrorCode(StrEnum):
    raw_missing = "raw_missing"
    raw_unreadable = "raw_unreadable"
    index_nested = "index_nested"
    index_not_empty = "index_not_empty"
    index_permission = "index_permission"
    disk_space = "disk_space"
    repository_locked = "repository_locked"
    parser_failure = "parser_failure"
    network_ai = "network_ai"
    unknown = "unknown"


SUPPORTED_EXTENSIONS = (
    TEXT_EXTENSIONS
    | IMAGE_EXTENSIONS
    | {".csv", ".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"}
)


def _format_profile(suffix: str) -> tuple[float, float, float]:
    if suffix in TEXT_EXTENSIONS or suffix == ".csv":
        return 0.02, 0.08, 0.35
    if suffix in {".docx", ".xlsx", ".xlsm", ".pptx"}:
        return 0.25, 0.9, 0.15
    if suffix == ".pdf":
        return 0.8, 3.0, 0.20
    if suffix in IMAGE_EXTENSIONS:
        return 1.0, 4.0, 0.08
    return 0.01, 0.05, 0.02


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _is_ignored(name: str, is_directory: bool) -> bool:
    if is_directory and name in DEFAULT_IGNORED_DIRECTORIES:
        return True
    return any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_IGNORED_GLOBS)


def estimate_repository(
    raw_repository: Path,
    index_repository: Path,
    *,
    ai_enabled: bool = False,
) -> RepositoryEstimate:
    raw = raw_repository.expanduser().resolve()
    index = index_repository.expanduser().resolve()
    blockers: list[str] = []
    warnings: list[str] = []

    if not raw.exists() or not raw.is_dir():
        blockers.append(OnboardingErrorCode.raw_missing.value)
    if raw == index or raw in index.parents or index in raw.parents:
        blockers.append(OnboardingErrorCode.index_nested.value)
    if index.exists():
        try:
            if not index.is_dir() or any(index.iterdir()):
                blockers.append(OnboardingErrorCode.index_not_empty.value)
        except (OSError, PermissionError):
            blockers.append(OnboardingErrorCode.index_permission.value)

    index_ancestor = _existing_ancestor(index)
    if not index_ancestor.is_dir() or not os.access(index_ancestor, os.W_OK):
        blockers.append(OnboardingErrorCode.index_permission.value)

    file_count = 0
    directory_count = 0
    supported = 0
    source_bytes = 0
    estimated_index_bytes = 0
    p50 = 0.0
    p95 = 0.0
    formats: Counter[str] = Counter()

    if raw.exists() and raw.is_dir():
        walk_failed = False

        def record_walk_error(error: OSError) -> None:
            nonlocal walk_failed
            walk_failed = True

        try:
            for root, directories, files in os.walk(
                raw,
                topdown=True,
                onerror=record_walk_error,
                followlinks=False,
            ):
                directories[:] = [
                    name
                    for name in directories
                    if not _is_ignored(name, True) and not (Path(root) / name).is_symlink()
                ]
                directory_count += len(directories)
                for name in files:
                    if _is_ignored(name, False):
                        continue
                    path = Path(root) / name
                    if path.is_symlink():
                        warnings.append("symlink_ignored")
                        continue
                    try:
                        size = path.stat().st_size
                    except (OSError, PermissionError):
                        warnings.append("file_unreadable")
                        continue
                    suffix = path.suffix.casefold() or "[none]"
                    file_count += 1
                    source_bytes += size
                    formats[suffix] += 1
                    if suffix in SUPPORTED_EXTENSIONS:
                        supported += 1
                    median, high, ratio = _format_profile(suffix)
                    p50 += median
                    p95 += high
                    estimated_index_bytes += max(4096, int(size * ratio))
        except (OSError, PermissionError):
            blockers.append(OnboardingErrorCode.raw_unreadable.value)
        if walk_failed:
            blockers.append(OnboardingErrorCode.raw_unreadable.value)

    estimated_index_bytes += directory_count * 64 * 1024
    required_free = max(256 * MIB, estimated_index_bytes * 3 + 32 * MIB)
    try:
        available_free = shutil.disk_usage(index_ancestor).free
    except OSError:
        available_free = 0
        blockers.append(OnboardingErrorCode.index_permission.value)
    if available_free < required_free:
        blockers.append(OnboardingErrorCode.disk_space.value)

    return RepositoryEstimate(
        raw_path=str(raw),
        index_path=str(index),
        file_count=file_count,
        directory_count=directory_count,
        supported_file_count=supported,
        unsupported_file_count=max(0, file_count - supported),
        format_counts=dict(sorted(formats.items())),
        total_source_bytes=source_bytes,
        estimated_index_bytes=estimated_index_bytes,
        required_free_bytes=required_free,
        available_free_bytes=available_free,
        estimated_seconds_p50=round(p50 + directory_count * 0.01, 1),
        estimated_seconds_p95=round(p95 + directory_count * 0.05, 1),
        estimated_ai_calls=file_count + directory_count if ai_enabled else 0,
        coefficient_version=ESTIMATE_COEFFICIENT_VERSION,
        blockers=sorted(set(blockers)),
        warnings=sorted(set(warnings)),
    )


def classify_onboarding_error(error: Exception) -> OnboardingErrorCode:
    message = str(error).casefold()
    name = type(error).__name__.casefold()
    if isinstance(error, FileNotFoundError) or "does not exist" in message:
        return OnboardingErrorCode.raw_missing
    if "nested" in message or "must be separate" in message:
        return OnboardingErrorCode.index_nested
    if "not empty" in message or "already an octopus" in message:
        return OnboardingErrorCode.index_not_empty
    if "space" in message or "disk" in message:
        return OnboardingErrorCode.disk_space
    if "permission" in message or "access" in message:
        return OnboardingErrorCode.index_permission
    if "lock" in message:
        return OnboardingErrorCode.repository_locked
    if "provider" in name or "api key" in message or "network" in message:
        return OnboardingErrorCode.network_ai
    if "parser" in name or "parse" in message or "ocr" in message:
        return OnboardingErrorCode.parser_failure
    return OnboardingErrorCode.unknown
