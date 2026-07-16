from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path

import pytest

from octopus.workspace_sources import (
    ArchivePolicy,
    SourceRef,
    materialize_source_ref,
    scan_archive,
)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_archive(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_scan_archive_models_members_and_materializes_read_only(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    raw.mkdir()
    archive = raw / "研究资料.zip"
    _write_archive(archive, {"论文/notes.txt": "中文证据".encode()})
    before = _tree_hash(raw)
    cache = tmp_path / "cache"

    result = scan_archive(archive, root_relative="研究资料.zip", cache_root=cache)

    assert len(result.members) == 1
    candidate = result.members[0]
    assert candidate.virtual_path == "研究资料.zip!/论文/notes.txt"
    assert candidate.source_ref.source_kind == "archive_member"
    assert candidate.source_ref.member_chain == ["论文/notes.txt"]
    assert candidate.materialized_path is not None
    assert candidate.materialized_path.read_text(encoding="utf-8") == "中文证据"
    assert not (candidate.materialized_path.stat().st_mode & stat.S_IWRITE)
    assert _tree_hash(raw) == before

    reopened = materialize_source_ref(
        raw,
        candidate.source_ref,
        cache_root=cache,
        expected_hash=candidate.content_hash,
    )
    assert reopened.read_bytes() == "中文证据".encode()


def test_scan_archive_rejects_unsafe_names_and_duplicate_members(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("../escape.txt", b"bad")
        value.writestr("safe.txt", b"one")
        value.writestr("safe.txt", b"two")

    result = scan_archive(archive, root_relative="unsafe.zip", cache_root=tmp_path / "cache")

    assert all("../" not in item.virtual_path for item in result.members)
    assert "archive_unsafe_member" in result.quality_flags
    duplicate = [item for item in result.members if "duplicate-2" in item.virtual_path]
    assert len(duplicate) == 1
    assert "archive_duplicate_name" in duplicate[0].quality_flags


def test_scan_archive_marks_limits_and_encrypted_members_without_extracting(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "limited.zip"
    with zipfile.ZipFile(archive, "w") as value:
        encrypted = zipfile.ZipInfo("secret.txt")
        value.writestr(encrypted, b"secret")
        value.writestr("large.txt", b"0123456789")
    payload = bytearray(archive.read_bytes())
    # Set the encrypted flag in both the local header and central directory.
    cursor = 0
    encrypted_header = True
    while True:
        cursor = payload.find(b"PK\x03\x04", cursor)
        if cursor < 0:
            break
        if encrypted_header:
            flags = int.from_bytes(payload[cursor + 6 : cursor + 8], "little") | 0x1
            payload[cursor + 6 : cursor + 8] = flags.to_bytes(2, "little")
            encrypted_header = False
        cursor += 4
    cursor = 0
    encrypted_header = True
    while True:
        cursor = payload.find(b"PK\x01\x02", cursor)
        if cursor < 0:
            break
        if encrypted_header:
            flags = int.from_bytes(payload[cursor + 8 : cursor + 10], "little") | 0x1
            payload[cursor + 8 : cursor + 10] = flags.to_bytes(2, "little")
            encrypted_header = False
        cursor += 4
    archive.write_bytes(payload)

    result = scan_archive(
        archive,
        root_relative="limited.zip",
        cache_root=tmp_path / "cache",
        policy=ArchivePolicy(max_member_bytes=5),
    )

    errors = {item.error_code for item in result.members}
    assert "archive_member_encrypted" in errors
    assert "archive_member_size_limit" in errors
    assert all(item.materialized_path is None for item in result.members)


def test_scan_archive_allows_one_nested_level_and_marks_second_level(tmp_path: Path) -> None:
    nested = tmp_path / "nested.zip"
    second = tmp_path / "second.zip"
    third = tmp_path / "third.zip"
    _write_archive(third, {"deep.txt": b"deep"})
    _write_archive(second, {"third.zip": third.read_bytes()})
    _write_archive(nested, {"inner.zip": second.read_bytes(), "top.txt": b"top"})

    result = scan_archive(nested, root_relative="nested.zip", cache_root=tmp_path / "cache")

    paths = {item.virtual_path for item in result.members}
    assert "nested.zip!/inner.zip" in paths
    assert "nested.zip!/inner.zip!/third.zip" in paths
    assert any("archive_nested_depth_limit" in flag for flag in result.quality_flags)


def test_scan_archive_supports_configured_second_nested_level(tmp_path: Path) -> None:
    deepest = tmp_path / "deepest.zip"
    middle = tmp_path / "middle.zip"
    archive = tmp_path / "nested-depth.zip"
    _write_archive(deepest, {"deep.txt": b"deep"})
    _write_archive(middle, {"deepest.zip": deepest.read_bytes()})
    _write_archive(archive, {"middle.zip": middle.read_bytes()})
    cache = tmp_path / "cache"
    policy = ArchivePolicy(max_nested_archives=2)

    result = scan_archive(
        archive,
        root_relative="nested-depth.zip",
        cache_root=cache,
        policy=policy,
    )

    deep = next(item for item in result.members if item.display_name == "deep.txt")
    assert deep.source_ref.archive_depth == 3
    assert materialize_source_ref(
        tmp_path,
        deep.source_ref,
        cache_root=cache,
        expected_hash=deep.content_hash,
        policy=policy,
    ).read_bytes() == b"deep"
    with pytest.raises(PermissionError, match="archive_nested_depth_limit"):
        materialize_source_ref(
            tmp_path,
            deep.source_ref,
            cache_root=cache,
            expected_hash=deep.content_hash,
        )


def test_scan_archive_applies_total_budget_across_nested_archives(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    archive = tmp_path / "nested-total.zip"
    _write_archive(first, {"first.txt": b"first"})
    _write_archive(second, {"second.txt": b"second"})
    first_payload = first.read_bytes()
    second_payload = second.read_bytes()
    _write_archive(
        archive,
        {
            "first.zip": first_payload,
            "second.zip": second_payload,
        },
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    existing = cache / "existing.bin"
    existing.write_bytes(b"keep")
    immediate_bytes = len(first_payload) + len(second_payload)

    result = scan_archive(
        archive,
        root_relative="nested-total.zip",
        cache_root=cache,
        policy=ArchivePolicy(
            max_total_bytes=immediate_bytes + len(b"first"),
            cache_max_bytes=1024 * 1024,
        ),
    )

    assert result.error_code == "archive_total_size_limit"
    assert result.members == []
    assert existing.read_bytes() == b"keep"
    assert list(cache.iterdir()) == [existing]


def test_scan_archive_applies_member_budget_across_nested_archives(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    archive = tmp_path / "nested-members.zip"
    _write_archive(first, {"first.txt": b"first"})
    _write_archive(second, {"second.txt": b"second"})
    _write_archive(
        archive,
        {
            "first.zip": first.read_bytes(),
            "second.zip": second.read_bytes(),
        },
    )
    cache = tmp_path / "cache"

    result = scan_archive(
        archive,
        root_relative="nested-members.zip",
        cache_root=cache,
        policy=ArchivePolicy(max_members=3),
    )

    assert result.error_code == "archive_member_count_limit"
    assert result.members == []
    assert not cache.exists() or list(cache.iterdir()) == []


def test_scan_archive_enforces_cache_budget_during_scan(tmp_path: Path) -> None:
    archive = tmp_path / "cache-limit.zip"
    _write_archive(
        archive,
        {
            "first.txt": b"first!",
            "second.txt": b"second",
        },
    )
    cache = tmp_path / "cache"

    result = scan_archive(
        archive,
        root_relative="cache-limit.zip",
        cache_root=cache,
        policy=ArchivePolicy(max_total_bytes=100, cache_max_bytes=6),
    )

    assert result.error_code == "archive_cache_size_limit"
    assert result.members == []
    assert not cache.exists() or list(cache.iterdir()) == []


def test_scan_archive_enforces_actual_extraction_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "actual-limit.zip"
    _write_archive(archive, {"declared.txt": b"a"})
    cache = tmp_path / "cache"
    original_open = zipfile.ZipFile.open

    def oversized_open(
        self: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: str = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ):
        if mode == "r":
            return io.BytesIO(b"ab")
        return original_open(
            self,
            name,
            mode=mode,
            pwd=pwd,
            force_zip64=force_zip64,
        )

    monkeypatch.setattr(zipfile.ZipFile, "open", oversized_open)

    result = scan_archive(
        archive,
        root_relative="actual-limit.zip",
        cache_root=cache,
        policy=ArchivePolicy(max_total_bytes=1),
    )

    assert result.error_code == "archive_total_size_limit"
    assert result.members == []
    assert not cache.exists() or list(cache.iterdir()) == []


def test_materialize_source_ref_rejects_changed_member_hash(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    _write_archive(archive, {"note.txt": b"old"})
    scan = scan_archive(archive, root_relative="source.zip", cache_root=tmp_path / "cache")
    source_ref: SourceRef = scan.members[0].source_ref
    _write_archive(archive, {"note.txt": b"new"})

    with pytest.raises(FileNotFoundError, match="changed"):
        materialize_source_ref(
            tmp_path,
            source_ref,
            cache_root=tmp_path / "cache",
            expected_hash=scan.members[0].content_hash,
        )
