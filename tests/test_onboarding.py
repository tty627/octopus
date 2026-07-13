from __future__ import annotations

import json
from pathlib import Path

import pytest

from octopus.activation import (
    ActivationSession,
    export_activation_records,
    summarize_activation_export,
    summarize_activation_exports,
)
from octopus.config import (
    create_repository,
    load_global_config,
    repository_config_path,
    repository_state_path,
)
from octopus.engine import UpdateEngine
from octopus.gui import _open_path, format_bytes, suggest_index_path
from octopus.models import UpdatePhase, UpdateProgress
from octopus.onboarding import (
    OnboardingErrorCode,
    classify_onboarding_error,
    estimate_repository,
)
from octopus.progress import CancellationToken, UpdateCancelledError
from octopus.sample_data import SAMPLE_SEARCH_TASKS, materialize_sample_repository
from octopus.search import SearchIndex
from octopus.transactions import load_run_report
from octopus.utils import sha256_file


def test_repository_estimate_is_read_only_and_reports_formats(tmp_path: Path) -> None:
    raw = tmp_path / "资料 空间"
    index = tmp_path / "索引 空间"
    raw.mkdir()
    (raw / "说明.md").write_text("项目说明", encoding="utf-8")
    (raw / "照片.png").write_bytes(b"not-a-real-image")
    (raw / "unknown.bin").write_bytes(b"opaque")

    estimate = estimate_repository(raw, index)

    assert estimate.file_count == 3
    assert estimate.supported_file_count == 2
    assert estimate.unsupported_file_count == 1
    assert estimate.estimated_ai_calls == 0
    assert estimate.required_free_bytes > estimate.estimated_index_bytes
    assert estimate.blockers == []
    assert not index.exists()


def test_repository_estimate_reports_path_blockers(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    nested = raw / "index"
    nested.mkdir()
    (nested / "occupied.txt").write_text("x", encoding="utf-8")

    estimate = estimate_repository(raw, nested)

    assert OnboardingErrorCode.index_nested.value in estimate.blockers
    assert OnboardingErrorCode.index_not_empty.value in estimate.blockers


def test_sample_repository_is_small_supported_and_never_overwritten(tmp_path: Path) -> None:
    sample = tmp_path / "示例"
    materialize_sample_repository(sample)

    files = sorted(path for path in sample.iterdir() if path.is_file())
    assert {path.suffix for path in files} == {".md", ".pdf", ".docx", ".xlsx", ".pptx", ".png"}
    assert sum(path.stat().st_size for path in files) < 5 * 1024 * 1024
    estimate = estimate_repository(sample, tmp_path / "示例索引")
    assert estimate.supported_file_count == 6
    assert SAMPLE_SEARCH_TASKS == ("预算审批", "项目里程碑", "负责人")
    with pytest.raises(FileExistsError):
        materialize_sample_repository(sample)


def test_sample_formats_index_and_search_fully_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    raw = materialize_sample_repository(tmp_path / "示例 资料")
    index = tmp_path / "示例 索引"
    before = {path.name: sha256_file(path) for path in raw.iterdir() if path.is_file()}
    create_repository(raw, index, "离线示例", ai_enabled=False, require_empty=True)

    stats = UpdateEngine(index).run(force_path="*")
    results = SearchIndex(index).search(SAMPLE_SEARCH_TASKS[0], limit=5)

    after = {path.name: sha256_file(path) for path in raw.iterdir() if path.is_file()}
    assert stats.leaf_updated == 5
    assert stats.failed == 0
    assert stats.ai_provider == "heuristic"
    assert load_run_report(index).ai_usage.calls == 0
    assert results
    assert before == after


def test_activation_record_is_local_and_contains_no_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    session = ActivationSession(sample_mode=True)
    session.stage("indexed")
    session.finish("success", file_count=6)

    text = session.path.read_text(encoding="utf-8")
    assert '"file_count": 6' in text
    assert "raw_path" not in text
    assert "query" not in text


def test_activation_export_filters_versions_and_summarizes_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    first = ActivationSession(sample_mode=True)
    first.stage("opened_result")
    first.finish("success", file_count=6)
    other = ActivationSession(sample_mode=False)
    other.record.version = "9.9.9"
    other.finish("failed", error_code="test_failure")

    output = tmp_path / "candidate.json"
    exported = export_activation_records(output)
    summary = summarize_activation_export(output)

    assert exported.record_count == 1
    assert exported.records[0].session_id == first.record.session_id
    assert summary.session_count == 1
    assert summary.success_count == 1
    assert summary.within_ten_minutes_count == 1
    assert not summary.meets_v04_session_thresholds


def test_activation_export_rejects_private_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    session = ActivationSession(sample_mode=True)
    payload = session.record.model_dump(mode="json")
    payload["query"] = "must not be exported"
    session.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden private fields"):
        export_activation_records(tmp_path / "candidate.json")


def test_activation_summary_rejects_duplicate_sessions_across_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    session = ActivationSession(sample_mode=True)
    session.finish("success", file_count=6)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    export_activation_records(first)
    second.write_bytes(first.read_bytes())

    with pytest.raises(ValueError, match="duplicate session IDs"):
        summarize_activation_exports([first, second])


def test_activation_summary_rechecks_export_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    session = ActivationSession(sample_mode=True)
    session.finish("success", file_count=6)
    exported = tmp_path / "candidate.json"
    export_activation_records(exported)
    payload = json.loads(exported.read_text(encoding="utf-8"))
    payload["records"][0]["raw_path"] = "C:/private"
    exported.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="forbidden private fields"):
        summarize_activation_export(exported)


def test_gui_path_and_size_helpers(tmp_path: Path) -> None:
    raw = tmp_path / "资料"
    assert suggest_index_path(raw) == tmp_path / "资料-Octopus-Index"
    (tmp_path / "资料-Octopus-Index").mkdir()
    assert suggest_index_path(raw) == tmp_path / "资料-Octopus-Index-2"
    assert format_bytes(2 * 1024**3) == "2.0 GiB"


def test_gui_path_opener_is_platform_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr("octopus.gui.sys.platform", "win32")
    monkeypatch.setattr(
        "octopus.gui.os.startfile", lambda path: opened.append(Path(path)), raising=False
    )
    _open_path(tmp_path)
    assert opened == [tmp_path]

    monkeypatch.setattr("octopus.gui.sys.platform", "linux")
    with pytest.raises(RuntimeError, match="only on Windows"):
        _open_path(tmp_path)


def test_repository_creation_can_disable_ai_and_rolls_back_local_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    raw = tmp_path / "raw"
    raw.mkdir()
    index = tmp_path / "index"

    config = create_repository(raw, index, ai_enabled=False, require_empty=True)
    assert not config.ai_policy.enabled

    second_index = tmp_path / "second-index"

    def fail_global_save(value: object) -> None:
        raise PermissionError("global config denied")

    monkeypatch.setattr("octopus.config.save_global_config", fail_global_save)
    with pytest.raises(PermissionError, match="global config denied"):
        create_repository(raw, second_index, require_empty=True)
    assert not repository_config_path(second_index).exists()
    assert not repository_state_path(second_index).exists()

    existing_index = tmp_path / "existing-index"
    existing_index.mkdir()
    keep = existing_index / "keep.txt"
    keep.write_text("user content", encoding="utf-8")
    with pytest.raises(PermissionError, match="global config denied"):
        create_repository(raw, existing_index)
    assert keep.read_text(encoding="utf-8") == "user content"
    assert not repository_config_path(existing_index).exists()


def test_update_cancellation_rolls_back_and_can_be_retried(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    source = raw / "需求.md"
    source.write_text("项目需求与预算审批", encoding="utf-8")
    before = sha256_file(source)
    token = CancellationToken()
    events: list[UpdateProgress] = []

    def progress(event: UpdateProgress) -> None:
        events.append(event)
        if event.phase == UpdatePhase.leaf and event.completed == 0:
            token.cancel()

    with pytest.raises(UpdateCancelledError):
        UpdateEngine(index).run(progress_callback=progress, cancellation_token=token)

    assert sha256_file(source) == before
    assert load_run_report(index).status == "cancelled"
    assert events[-1].phase == UpdatePhase.cancelled
    config = load_global_config()
    assert config.active_repository_id in config.repositories

    completed: list[UpdateProgress] = []
    UpdateEngine(index).run(progress_callback=completed.append)
    assert completed[-1].phase == UpdatePhase.complete
    assert load_run_report(index).status == "success"
    assert sha256_file(source) == before


@pytest.mark.parametrize(
    ("cancel_phase", "cancel_completed"),
    [
        (UpdatePhase.scanning, None),
        (UpdatePhase.leaf, 1),
        (UpdatePhase.foldernode, 0),
    ],
)
def test_cancellation_in_each_precommit_phase_preserves_raw_and_report(
    repository: tuple[Path, Path, object],
    cancel_phase: UpdatePhase,
    cancel_completed: int | None,
) -> None:
    raw, index, _ = repository
    source = raw / "cancel phase.md"
    source.write_text("budget approval milestone", encoding="utf-8")
    before = sha256_file(source)
    token = CancellationToken()

    def progress(event: UpdateProgress) -> None:
        matches_completed = cancel_completed is None or event.completed == cancel_completed
        if event.phase == cancel_phase and matches_completed:
            token.cancel()

    with pytest.raises(UpdateCancelledError):
        UpdateEngine(index).run(
            force_path="*",
            progress_callback=progress,
            cancellation_token=token,
        )

    assert sha256_file(source) == before
    assert load_run_report(index).status == "cancelled"


def test_commit_phase_is_noncancellable_and_progress_is_monotonic(
    repository: tuple[Path, Path, object],
) -> None:
    raw, index, _ = repository
    (raw / "commit.md").write_text("commit boundary", encoding="utf-8")
    token = CancellationToken()
    events: list[UpdateProgress] = []

    def progress(event: UpdateProgress) -> None:
        events.append(event)
        if event.phase == UpdatePhase.committing:
            assert not event.cancellable
            token.cancel()

    UpdateEngine(index).run(
        force_path="*",
        progress_callback=progress,
        cancellation_token=token,
    )

    assert load_run_report(index).status == "success"
    assert events[-1].phase == UpdatePhase.complete
    assert [event.percent for event in events] == sorted(event.percent for event in events)
    assert all(
        not event.cancellable
        for event in events
        if event.phase
        in {UpdatePhase.committing, UpdatePhase.search_rebuild, UpdatePhase.complete}
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError("raw does not exist"), OnboardingErrorCode.raw_missing),
        (ValueError("paths must be separate, non-nested paths"), OnboardingErrorCode.index_nested),
        (PermissionError("access denied"), OnboardingErrorCode.index_permission),
        (RuntimeError("repository lock is active"), OnboardingErrorCode.repository_locked),
        (RuntimeError("network provider failed"), OnboardingErrorCode.network_ai),
    ],
)
def test_onboarding_errors_are_stable(error: Exception, expected: OnboardingErrorCode) -> None:
    assert classify_onboarding_error(error) == expected
