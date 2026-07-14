from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from octopus.diagnostics import (
    CONSENT_ENTRY,
    DIAGNOSTIC_ENTRY,
    create_diagnostic_bundle,
    diagnostic_summary,
    inspect_diagnostic_bundle,
    prepare_diagnostic_share,
)
from octopus.engine import UpdateEngine
from octopus.models import RunReport, utc_now
from octopus.transactions import write_run_report


def test_diagnostic_bundle_is_local_content_free_and_path_free(
    repository: tuple[Path, Path, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw, index, config = repository
    source = raw / "机密 客户" / "令牌.txt"
    source.parent.mkdir()
    source.write_text("private-query API_KEY=do-not-leak", encoding="utf-8")
    UpdateEngine(index).run(force_path="*")
    write_run_report(
        index,
        RunReport(
            run_id="diagnostic-error",
            repository_id=config.repository.raw_repo_id,
            started_at=utc_now(),
            finished_at=utc_now(),
            status="partial",
            errors=[
                {
                    "node_id": "sensitive-node",
                    "code": str(source.resolve()),
                    "message": "API_KEY=do-not-leak private-query",
                }
            ],
        ),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "parent-process-secret")

    output = tmp_path / "diagnostics.zip"
    create_diagnostic_bundle(output, [index])
    bundle, consent = inspect_diagnostic_bundle(output)
    archive_text = output.read_bytes().decode("latin-1", errors="ignore")
    with zipfile.ZipFile(output) as archive:
        payload_text = archive.read(DIAGNOSTIC_ENTRY).decode("utf-8")

    assert consent is None
    assert bundle.local_only
    assert not bundle.contains_paths
    assert not bundle.contains_file_content
    assert bundle.repositories[0].repository_ref == "repository-1"
    assert bundle.repositories[0].recent_runs[-1].error_codes == ["other"]
    for forbidden in (
        str(raw.resolve()),
        str(index.resolve()),
        config.repository.raw_repo_id,
        "机密 客户",
        "令牌.txt",
        "private-query",
        "do-not-leak",
        "parent-process-secret",
        "sensitive-node",
    ):
        assert forbidden not in payload_text
        assert forbidden not in archive_text


def test_share_preparation_requires_consent_and_never_uploads(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    _, index, _ = repository
    local = tmp_path / "local.zip"
    shared = tmp_path / "shared.zip"
    create_diagnostic_bundle(local, [index])

    with pytest.raises(PermissionError, match="Explicit consent"):
        prepare_diagnostic_share(local, shared, consent=False)
    assert not shared.exists()

    prepare_diagnostic_share(local, shared, consent=True)
    bundle, receipt = inspect_diagnostic_bundle(shared)
    summary = diagnostic_summary(shared)

    assert bundle.local_only
    assert receipt is not None and receipt.explicit_consent
    assert summary["share_consent_recorded"] is True
    with zipfile.ZipFile(shared) as archive:
        assert set(archive.namelist()) == {DIAGNOSTIC_ENTRY, CONSENT_ENTRY}


def test_diagnostic_reader_rejects_unknown_or_oversized_archive_entries(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    _, index, _ = repository
    valid = tmp_path / "valid.zip"
    create_diagnostic_bundle(valid, [index])
    payload = json.dumps({"not": "a diagnostic"}).encode()
    invalid = tmp_path / "invalid.zip"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr(DIAGNOSTIC_ENTRY, payload)
        archive.writestr("raw-content.txt", "must be rejected")

    with pytest.raises(ValueError, match="unexpected entries"):
        inspect_diagnostic_bundle(invalid)


def test_diagnostic_creation_refuses_to_overwrite_existing_output(
    repository: tuple[Path, Path, object], tmp_path: Path
) -> None:
    _, index, _ = repository
    output = tmp_path / "existing.zip"
    output.write_bytes(b"keep me")

    with pytest.raises(FileExistsError):
        create_diagnostic_bundle(output, [index])

    assert output.read_bytes() == b"keep me"
