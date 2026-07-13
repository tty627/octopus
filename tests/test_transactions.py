from __future__ import annotations

from pathlib import Path

import pytest

from octopus.models import RunReport, TransactionStatus, utc_now
from octopus.transactions import (
    IndexTransaction,
    load_run_report,
    mark_transaction_complete,
    recover_transactions,
    write_run_report,
)


def _index(tmp_path: Path) -> Path:
    index = tmp_path / "index"
    (index / ".octopus").mkdir(parents=True)
    return index


def test_transaction_commits_manifest_last_and_cleans_payload(tmp_path: Path) -> None:
    index = _index(tmp_path)
    target = index / "leaf.md"
    manifest = index / ".octopus" / "repository-state.json"
    target.write_text("old", encoding="utf-8")
    manifest.write_text("old manifest", encoding="utf-8")

    phases: list[str] = []
    transaction = IndexTransaction(index, run_id="commit-run", failure_injector=phases.append)
    transaction.write_text(target, "new")
    transaction.commit(manifest, "new manifest")

    assert target.read_text(encoding="utf-8") == "new"
    assert manifest.read_text(encoding="utf-8") == "new manifest"
    assert phases == [
        "before:leaf.md",
        "after:leaf.md",
        "before:.octopus/repository-state.json",
        "after:.octopus/repository-state.json",
        "after_manifest",
    ]
    assert transaction.record.status == TransactionStatus.recovery_required
    mark_transaction_complete(index, transaction.run_id)
    completed = IndexTransaction.load(index, transaction.record_path)
    assert completed.record.status == TransactionStatus.committed
    assert not completed.stage_directory.exists()
    assert not completed.backup_directory.exists()


def test_staging_batches_transaction_record_writes(tmp_path: Path) -> None:
    index = _index(tmp_path)
    transaction = IndexTransaction(index, run_id="batched-stage")
    for number in range(100):
        transaction.write_text(index / "nodes" / f"{number}.md", "value")

    persisted = IndexTransaction.load(index, transaction.record_path)
    assert persisted.record.operations == []
    manifest = index / ".octopus" / "repository-state.json"
    transaction.commit(manifest, "{}")
    committed = IndexTransaction.load(index, transaction.record_path)
    assert len(committed.record.operations) == 101


def test_recovery_replays_durable_intent_after_process_interruption(tmp_path: Path) -> None:
    index = _index(tmp_path)
    target = index / "leaf.md"
    target.write_text("old", encoding="utf-8")
    manifest = index / ".octopus" / "repository-state.json"
    manifest.write_text("old manifest", encoding="utf-8")

    def interrupt(phase: str) -> None:
        if phase == "after:leaf.md":
            raise KeyboardInterrupt("process interrupted")

    transaction = IndexTransaction(index, run_id="interrupted", failure_injector=interrupt)
    transaction.write_text(target, "new")
    with pytest.raises(KeyboardInterrupt, match="process interrupted"):
        transaction.commit(manifest, "new manifest")

    assert target.read_text(encoding="utf-8") == "new"
    assert recover_transactions(index) == ["rolled-back:interrupted"]
    assert target.read_text(encoding="utf-8") == "old"
    assert manifest.read_text(encoding="utf-8") == "old manifest"


@pytest.mark.parametrize(
    "failure_phase",
    [
        "before:leaf.md",
        "after:leaf.md",
        "before:.octopus/repository-state.json",
    ],
)
def test_transaction_rolls_back_failure_before_manifest(tmp_path: Path, failure_phase: str) -> None:
    index = _index(tmp_path)
    target = index / "leaf.md"
    manifest = index / ".octopus" / "repository-state.json"
    target.write_text("old", encoding="utf-8")
    manifest.write_text("old manifest", encoding="utf-8")

    def fail(phase: str) -> None:
        if phase == failure_phase:
            raise RuntimeError("injected crash")

    transaction = IndexTransaction(index, run_id="rollback-run", failure_injector=fail)
    transaction.write_text(target, "new")
    with pytest.raises(RuntimeError, match="injected crash"):
        transaction.commit(manifest, "new manifest")

    assert target.read_text(encoding="utf-8") == "old"
    assert manifest.read_text(encoding="utf-8") == "old manifest"
    assert transaction.record.status == TransactionStatus.rolled_back
    assert recover_transactions(index) == []


def test_manifest_commit_is_completed_by_recovery(tmp_path: Path) -> None:
    index = _index(tmp_path)
    target = index / "leaf.md"
    manifest = index / ".octopus" / "repository-state.json"

    def fail(phase: str) -> None:
        if phase == "after_manifest":
            raise RuntimeError("derived state crash")

    transaction = IndexTransaction(index, run_id="recovery-run", failure_injector=fail)
    transaction.write_text(target, "new")
    with pytest.raises(RuntimeError, match="derived state crash"):
        transaction.commit(manifest, "new manifest")

    assert target.read_text(encoding="utf-8") == "new"
    assert manifest.read_text(encoding="utf-8") == "new manifest"
    assert recover_transactions(index) == ["complete-derived:recovery-run"]
    mark_transaction_complete(index, "recovery-run")
    assert recover_transactions(index) == []


def test_append_only_intent_recovers_uncaught_process_loss(tmp_path: Path) -> None:
    index = _index(tmp_path)
    target = index / "leaf.md"
    manifest = index / ".octopus" / "repository-state.json"
    target.write_text("old", encoding="utf-8")
    manifest.write_text("old manifest", encoding="utf-8")

    class SimulatedPowerLoss(BaseException):
        pass

    def lose_power(phase: str) -> None:
        if phase == "after:leaf.md":
            raise SimulatedPowerLoss()

    transaction = IndexTransaction(index, run_id="power-loss", failure_injector=lose_power)
    transaction.write_text(target, "new")
    with pytest.raises(SimulatedPowerLoss):
        transaction.commit(manifest, "new manifest")

    assert target.read_text(encoding="utf-8") == "new"
    assert transaction.intent_path.exists()
    assert recover_transactions(index) == ["rolled-back:power-loss"]
    assert target.read_text(encoding="utf-8") == "old"
    assert manifest.read_text(encoding="utf-8") == "old manifest"


def test_transaction_delete_escape_and_immutable_run_report(tmp_path: Path) -> None:
    index = _index(tmp_path)
    target = index / "obsolete.md"
    manifest = index / ".octopus" / "repository-state.json"
    target.write_text("remove", encoding="utf-8")
    transaction = IndexTransaction(index, run_id="delete-run")
    transaction.schedule_delete(target)
    with pytest.raises(PermissionError):
        transaction.write_text(tmp_path / "outside.md", "no")
    transaction.commit(manifest, "{}")
    assert not target.exists()
    mark_transaction_complete(index, "delete-run")

    report = RunReport(
        run_id="report-run",
        repository_id="repo",
        started_at=utc_now(),
        finished_at=utc_now(),
        status="success",
    )
    path = write_run_report(index, report)
    assert path.exists()
    assert load_run_report(index).run_id == "report-run"
    assert load_run_report(index, "report-run").status == "success"
    with pytest.raises(FileExistsError):
        write_run_report(index, report)
