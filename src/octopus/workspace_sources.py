from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import time
import uuid
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import AliasChoices, Field

from .models import OctopusModel

SourceKind = Literal["physical", "archive", "archive_member"]
LocatorKind = Literal[
    "page",
    "paragraph",
    "table",
    "sheet",
    "slide",
    "image",
    "text_line",
    "document",
]


class SourceRef(OctopusModel):
    kind: SourceKind = Field(
        default="physical",
        validation_alias=AliasChoices("kind", "source_kind"),
    )
    workspace_path: str
    virtual_path: str
    container_path: str = ""
    member_path: str = ""
    member_chain: list[str] = Field(default_factory=list)
    member_indexes: list[int] = Field(default_factory=list)
    archive_depth: int = Field(default=0, ge=0, le=3)
    stable_id: str = ""

    @property
    def source_kind(self) -> SourceKind:
        return self.kind


class EvidenceLocator(OctopusModel):
    kind: LocatorKind = "document"
    page_number: int | None = Field(default=None, ge=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    table_index: int | None = Field(default=None, ge=1)
    sheet_name: str = ""
    cell_range: str = ""
    slide_number: int | None = Field(default=None, ge=1)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    label: str = ""


@dataclass(frozen=True)
class ArchivePolicy:
    max_members: int = 10_000
    max_member_bytes: int = 100 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_nested_archives: int = 1
    cache_ttl_seconds: int = 24 * 60 * 60
    cache_max_bytes: int = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ArchiveCandidate:
    virtual_path: str
    display_name: str
    extension: str
    size_bytes: int
    compressed_size: int
    modified_at: str
    source_ref: SourceRef
    content_hash: str = ""
    materialized_path: Path | None = None
    quality_flags: list[str] = field(default_factory=list)
    error_code: str = ""


@dataclass(frozen=True)
class ArchiveScan:
    members: list[ArchiveCandidate] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    error_code: str = ""
    error: str = ""


class ArchiveLimitError(RuntimeError):
    pass


class _ArchiveScanLimitError(ArchiveLimitError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _ArchiveScanBudget:
    policy: ArchivePolicy
    cache_root: Path
    member_count: int = 0
    declared_bytes: int = 0
    actual_bytes: int = 0
    cache_bytes: int = 0
    cache_files: dict[Path, os.stat_result] = field(default_factory=dict)
    protected_paths: set[Path] = field(default_factory=set)
    created_paths: set[Path] = field(default_factory=set)

    @classmethod
    def from_cache(cls, cache_root: Path, policy: ArchivePolicy) -> _ArchiveScanBudget:
        cache_files: dict[Path, os.stat_result] = {}
        if cache_root.is_dir():
            for path in cache_root.iterdir():
                if not path.is_file():
                    continue
                try:
                    cache_files[path] = path.stat()
                except OSError:
                    continue
        return cls(
            policy=policy,
            cache_root=cache_root,
            cache_bytes=sum(item.st_size for item in cache_files.values()),
            cache_files=cache_files,
        )

    def reserve_archive(self, files: list[tuple[int, zipfile.ZipInfo]]) -> None:
        member_count = self.member_count + len(files)
        if member_count > self.policy.max_members:
            raise _ArchiveScanLimitError(
                "archive_member_count_limit",
                "Archive tree exceeded the cumulative member budget",
            )
        declared_bytes = self.declared_bytes + sum(
            max(0, info.file_size) for _, info in files
        )
        if declared_bytes > self.policy.max_total_bytes:
            raise _ArchiveScanLimitError(
                "archive_total_size_limit",
                "Archive tree exceeded the cumulative declared-size budget",
            )
        self.member_count = member_count
        self.declared_bytes = declared_bytes

    def consume_actual(self, amount: int) -> None:
        actual_bytes = self.actual_bytes + max(0, amount)
        if actual_bytes > self.policy.max_total_bytes:
            raise _ArchiveScanLimitError(
                "archive_total_size_limit",
                "Archive tree exceeded the cumulative extraction budget",
            )
        self.actual_bytes = actual_bytes

    def prepare_cache_write(self, size: int) -> None:
        required = max(0, size)
        for path, path_stat in sorted(
            self.cache_files.items(), key=lambda item: item[1].st_atime
        ):
            if self.cache_bytes + required <= self.policy.cache_max_bytes:
                break
            if path in self.protected_paths:
                continue
            try:
                path.chmod(0o666)
                path.unlink()
            except OSError:
                continue
            self.cache_bytes -= path_stat.st_size
            self.cache_files.pop(path, None)
        if self.cache_bytes + required > self.policy.cache_max_bytes:
            raise _ArchiveScanLimitError(
                "archive_cache_size_limit",
                "Archive scan exceeded the materialized-cache budget",
            )

    def check_temporary_size(self, size: int) -> None:
        if self.cache_bytes + max(0, size) > self.policy.cache_max_bytes:
            raise _ArchiveScanLimitError(
                "archive_cache_size_limit",
                "Archive scan exceeded the materialized-cache budget",
            )

    def register_materialized(self, path: Path, *, created: bool) -> None:
        if created:
            self.created_paths.add(path)
        try:
            path_stat = path.stat()
        except OSError as error:
            raise _ArchiveScanLimitError(
                "archive_cache_size_limit",
                "Materialized archive member is unavailable",
            ) from error
        previous = self.cache_files.get(path)
        if previous is None:
            self.cache_bytes += path_stat.st_size
        else:
            self.cache_bytes += path_stat.st_size - previous.st_size
        self.cache_files[path] = path_stat
        self.protected_paths.add(path)
        if self.cache_bytes > self.policy.cache_max_bytes:
            raise _ArchiveScanLimitError(
                "archive_cache_size_limit",
                "Archive scan exceeded the materialized-cache budget",
            )

    def rollback_created(self) -> None:
        for path in self.created_paths:
            try:
                path.chmod(0o666)
                path.unlink()
            except OSError:
                continue


def physical_source_ref(relative_path: str, *, archive: bool = False) -> SourceRef:
    return SourceRef(
        kind="archive" if archive else "physical",
        workspace_path=relative_path,
        virtual_path=relative_path,
        container_path=relative_path if archive else "",
    )


def _zip_datetime(info: zipfile.ZipInfo) -> str:
    try:
        value = datetime(*info.date_time, tzinfo=UTC)
    except (TypeError, ValueError):
        value = datetime.fromtimestamp(0, tz=UTC)
    return value.isoformat()


def _legacy_filename(name: str) -> tuple[str, bool]:
    if not name:
        return name, False
    suspicious = any("\u2500" <= character <= "\u259f" for character in name)
    suspicious = suspicious or any("\x80" <= character <= "\xff" for character in name)
    if not suspicious:
        return name, False
    try:
        candidate = name.encode("cp437").decode("cp936")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name, False
    cjk = sum("\u3400" <= character <= "\u9fff" for character in candidate)
    if cjk < 1:
        return name, False
    return candidate, candidate != name


def _safe_member_name(name: str) -> str | None:
    if not name or "\x00" in name:
        return None
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//"):
        return None
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    if ":" in parts[0]:
        return None
    return "/".join(parts)


def _is_link(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return bool(unix_mode and stat.S_ISLNK(unix_mode))


def _supported_compression(info: zipfile.ZipInfo) -> bool:
    methods = {
        zipfile.ZIP_STORED,
        zipfile.ZIP_DEFLATED,
        zipfile.ZIP_BZIP2,
        zipfile.ZIP_LZMA,
    }
    return info.compress_type in methods


def _member_error(info: zipfile.ZipInfo, policy: ArchivePolicy) -> tuple[str, list[str]]:
    if info.flag_bits & 0x1:
        return "archive_member_encrypted", ["archive_member_encrypted"]
    if _is_link(info):
        return "archive_member_link", ["archive_member_link"]
    if not _supported_compression(info):
        return "archive_compression_unsupported", ["archive_compression_unsupported"]
    if info.file_size > policy.max_member_bytes:
        return "archive_member_size_limit", ["archive_member_size_limit"]
    if (
        info.file_size
        and info.file_size / max(1, info.compress_size) > policy.max_compression_ratio
    ):
        return "archive_compression_ratio_limit", ["archive_compression_ratio_limit"]
    return "", []


def _write_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    cache_root: Path,
    suffix: str,
    policy: ArchivePolicy,
    scan_budget: _ArchiveScanBudget | None = None,
) -> tuple[Path, str]:
    cache_root.mkdir(parents=True, exist_ok=True)
    if scan_budget is not None:
        scan_budget.prepare_cache_write(info.file_size)
    temporary = cache_root / f".tmp-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    total = 0
    try:
        with archive.open(info, "r") as source, temporary.open("wb") as destination:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if scan_budget is not None:
                    scan_budget.consume_actual(len(chunk))
                if total > policy.max_member_bytes or total > info.file_size + 1024:
                    raise ArchiveLimitError("Archive member exceeded its declared size or budget")
                if scan_budget is not None:
                    scan_budget.check_temporary_size(total)
                digest.update(chunk)
                destination.write(chunk)
        if total != info.file_size:
            raise zipfile.BadZipFile("Archive member size does not match its directory entry")
        content_hash = digest.hexdigest()
        target = cache_root / f"{content_hash}{suffix.casefold()}"
        created = not target.exists()
        if not created:
            temporary.unlink(missing_ok=True)
            os.utime(target, None)
        else:
            temporary.replace(target)
            with suppress(OSError):
                target.chmod(0o444)
        if scan_budget is not None:
            scan_budget.register_materialized(target, created=created)
        return target, content_hash
    finally:
        temporary.unlink(missing_ok=True)


def _candidate_for_error(
    *,
    virtual_path: str,
    display_name: str,
    info: zipfile.ZipInfo,
    source_ref: SourceRef,
    quality_flags: list[str],
    error_code: str,
) -> ArchiveCandidate:
    return ArchiveCandidate(
        virtual_path=virtual_path,
        display_name=display_name,
        extension=Path(display_name).suffix.casefold(),
        size_bytes=max(0, info.file_size),
        compressed_size=max(0, info.compress_size),
        modified_at=_zip_datetime(info),
        source_ref=source_ref,
        quality_flags=sorted(set(quality_flags)),
        error_code=error_code,
    )


def _scan_zip(
    archive: zipfile.ZipFile,
    *,
    root_relative: str,
    base_virtual: str,
    cache_root: Path,
    policy: ArchivePolicy,
    chain: list[str],
    indexes: list[int],
    nested_level: int,
    scan_budget: _ArchiveScanBudget,
) -> tuple[list[ArchiveCandidate], list[str], str]:
    candidates: list[ArchiveCandidate] = []
    flags: list[str] = []
    infos = archive.infolist()
    files = [(index, info) for index, info in enumerate(infos) if not info.is_dir()]
    scan_budget.reserve_archive(files)
    decoded_counts: dict[str, int] = {}
    for index, info in files:
        decoded_name, recovered = _legacy_filename(info.filename)
        safe_name = _safe_member_name(decoded_name)
        if safe_name is None:
            flags.append("archive_unsafe_member")
            continue
        occurrence = decoded_counts.get(safe_name, 0) + 1
        decoded_counts[safe_name] = occurrence
        virtual_name = safe_name if occurrence == 1 else f"{safe_name}#duplicate-{occurrence}"
        virtual_path = f"{base_virtual}!/{virtual_name}"
        member_chain = [*chain, safe_name]
        member_indexes = [*indexes, index]
        source_ref = SourceRef(
            kind="archive_member",
            workspace_path=root_relative,
            virtual_path=virtual_path,
            container_path=root_relative,
            member_path=safe_name,
            member_chain=member_chain,
            member_indexes=member_indexes,
            archive_depth=len(member_chain),
        )
        member_flags: list[str] = []
        if recovered:
            member_flags.append("archive_filename_cp936_recovered")
            flags.append("archive_filename_encoding_risk")
        if occurrence > 1:
            member_flags.append("archive_duplicate_name")
            flags.append("archive_duplicate_name")
        error_code, error_flags = _member_error(info, policy)
        member_flags.extend(error_flags)
        if error_code:
            candidates.append(
                _candidate_for_error(
                    virtual_path=virtual_path,
                    display_name=Path(safe_name).name,
                    info=info,
                    source_ref=source_ref,
                    quality_flags=member_flags,
                    error_code=error_code,
                )
            )
            continue
        suffix = Path(safe_name).suffix.casefold()
        try:
            materialized, content_hash = _write_member(
                archive,
                info,
                cache_root,
                suffix,
                policy,
                scan_budget,
            )
        except _ArchiveScanLimitError:
            raise
        except (ArchiveLimitError, OSError, RuntimeError, zipfile.BadZipFile) as error:
            code = (
                "archive_crc_error"
                if isinstance(error, zipfile.BadZipFile)
                else "archive_member_read_failed"
            )
            candidates.append(
                _candidate_for_error(
                    virtual_path=virtual_path,
                    display_name=Path(safe_name).name,
                    info=info,
                    source_ref=source_ref,
                    quality_flags=[*member_flags, code],
                    error_code=code,
                )
            )
            flags.append(code)
            continue
        candidate = ArchiveCandidate(
            virtual_path=virtual_path,
            display_name=Path(safe_name).name,
            extension=suffix,
            size_bytes=info.file_size,
            compressed_size=info.compress_size,
            modified_at=_zip_datetime(info),
            source_ref=source_ref,
            content_hash=content_hash,
            materialized_path=materialized,
            quality_flags=sorted(set(member_flags)),
        )
        candidates.append(candidate)
        if suffix != ".zip" or nested_level >= policy.max_nested_archives:
            if suffix == ".zip" and nested_level >= policy.max_nested_archives:
                flags.append("archive_nested_depth_limit")
            continue
        try:
            with zipfile.ZipFile(materialized) as nested:
                nested_candidates, nested_flags, nested_error = _scan_zip(
                    nested,
                    root_relative=root_relative,
                    base_virtual=virtual_path,
                    cache_root=cache_root,
                    policy=policy,
                    chain=member_chain,
                    indexes=member_indexes,
                    nested_level=nested_level + 1,
                    scan_budget=scan_budget,
                )
            candidates.extend(nested_candidates)
            flags.extend(nested_flags)
            if nested_error:
                flags.append(nested_error)
        except _ArchiveScanLimitError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile):
            flags.append("archive_nested_invalid")
    return candidates, sorted(set(flags)), ""


def scan_archive(
    path: Path,
    *,
    root_relative: str,
    cache_root: Path,
    policy: ArchivePolicy | None = None,
) -> ArchiveScan:
    active_policy = policy or ArchivePolicy()
    cleanup_materialized_cache(cache_root, active_policy)
    scan_budget = _ArchiveScanBudget.from_cache(cache_root, active_policy)
    try:
        with zipfile.ZipFile(path) as archive:
            members, flags, error_code = _scan_zip(
                archive,
                root_relative=root_relative,
                base_virtual=root_relative,
                cache_root=cache_root,
                policy=active_policy,
                chain=[],
                indexes=[],
                nested_level=0,
                scan_budget=scan_budget,
            )
        return ArchiveScan(members=members, quality_flags=flags, error_code=error_code)
    except _ArchiveScanLimitError as error:
        scan_budget.rollback_created()
        return ArchiveScan(
            quality_flags=[error.code],
            error_code=error.code,
            error=str(error)[:500],
        )
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        scan_budget.rollback_created()
        return ArchiveScan(
            quality_flags=["archive_invalid"],
            error_code="archive_invalid",
            error=f"{type(error).__name__}: {error}"[:500],
        )
    except Exception:
        scan_budget.rollback_created()
        raise


def cleanup_materialized_cache(cache_root: Path, policy: ArchivePolicy | None = None) -> None:
    active_policy = policy or ArchivePolicy()
    if not cache_root.is_dir():
        return
    now = time.time()
    files: list[tuple[Path, os.stat_result]] = []
    for path in cache_root.iterdir():
        if not path.is_file():
            continue
        try:
            path_stat = path.stat()
        except OSError:
            continue
        if (
            path.name.startswith(".tmp-")
            or now - path_stat.st_mtime > active_policy.cache_ttl_seconds
        ):
            try:
                path.chmod(0o666)
                path.unlink()
            except OSError:
                pass
            continue
        files.append((path, path_stat))
    total = sum(item.st_size for _, item in files)
    for path, path_stat in sorted(files, key=lambda item: item[1].st_atime):
        if total <= active_policy.cache_max_bytes:
            break
        try:
            path.chmod(0o666)
            path.unlink()
            total -= path_stat.st_size
        except OSError:
            continue


def _member_by_index(
    archive: zipfile.ZipFile,
    index: int,
    expected_name: str,
) -> zipfile.ZipInfo:
    infos = archive.infolist()
    if index < 0 or index >= len(infos):
        raise FileNotFoundError("Archive member no longer exists")
    info = infos[index]
    decoded, _ = _legacy_filename(info.filename)
    if _safe_member_name(decoded) != expected_name:
        raise FileNotFoundError("Archive member identity no longer matches")
    return info


def materialize_source_ref(
    root: Path,
    source_ref: SourceRef,
    *,
    cache_root: Path,
    expected_hash: str = "",
    policy: ArchivePolicy | None = None,
) -> Path:
    active_policy = policy or ArchivePolicy()
    if source_ref.source_kind != "archive_member":
        source = (root / source_ref.workspace_path).resolve()
        if source != root and root not in source.parents:
            raise ValueError("Source path escapes the workspace")
        return source
    if not source_ref.member_chain or len(source_ref.member_chain) != len(
        source_ref.member_indexes
    ):
        raise ValueError("Archive source reference is incomplete")
    if len(source_ref.member_chain) > active_policy.max_nested_archives + 1:
        raise PermissionError("archive_nested_depth_limit")
    source = (root / source_ref.container_path).resolve()
    if source != root and root not in source.parents:
        raise ValueError("Archive path escapes the workspace")
    nested_payload: bytes | None = None
    for depth, (name, index) in enumerate(
        zip(source_ref.member_chain, source_ref.member_indexes, strict=True)
    ):
        container: str | Path | io.BytesIO
        container = source if nested_payload is None else io.BytesIO(nested_payload)
        with zipfile.ZipFile(container) as archive:
            info = _member_by_index(archive, index, name)
            error_code, _ = _member_error(info, active_policy)
            if error_code:
                raise PermissionError(error_code)
            if depth == len(source_ref.member_chain) - 1:
                target, content_hash = _write_member(
                    archive,
                    info,
                    cache_root,
                    Path(name).suffix.casefold(),
                    active_policy,
                )
                if expected_hash and content_hash != expected_hash:
                    raise FileNotFoundError("Archive member content has changed")
                return target
            with archive.open(info, "r") as member:
                nested_payload = member.read(active_policy.max_member_bytes + 1)
            if len(nested_payload) > active_policy.max_member_bytes:
                raise ArchiveLimitError("Nested archive exceeds the member budget")
    raise FileNotFoundError("Archive member not found")


def cache_expiry(path: Path, policy: ArchivePolicy | None = None) -> str:
    active_policy = policy or ArchivePolicy()
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        modified = datetime.now(UTC)
    return (modified + timedelta(seconds=active_policy.cache_ttl_seconds)).isoformat()


def clear_materialized_cache(cache_root: Path) -> None:
    if not cache_root.exists():
        return
    resolved = cache_root.resolve()
    if resolved.name == "" or resolved.parent == resolved:
        raise ValueError("Refusing to clear an unsafe cache path")
    shutil.rmtree(resolved)
