from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from .config import octopus_dir
from .models import (
    RunReport,
    TransactionOperation,
    TransactionRecord,
    TransactionStatus,
    utc_now,
)
from .utils import atomic_write_json, load_json

FailureInjector = Callable[[str], None]


class IndexTransaction:
    def __init__(
        self,
        index_repository: Path,
        run_id: str | None = None,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.index = index_repository.resolve()
        self.run_id = run_id or uuid.uuid4().hex
        self.directory = octopus_dir(self.index) / "transactions" / self.run_id
        self.stage_directory = self.directory / "stage"
        self.backup_directory = self.directory / "backup"
        self.record_path = self.directory / "record.json"
        self.intent_path = self.directory / "intent.jsonl"
        self.failure_injector = failure_injector
        self.record = TransactionRecord(run_id=self.run_id)
        self.operations_by_path: dict[str, TransactionOperation] = {}
        self.stage_directory.mkdir(parents=True, exist_ok=False)
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        self._save_record()

    @classmethod
    def load(cls, index_repository: Path, record_path: Path) -> IndexTransaction:
        transaction = object.__new__(cls)
        transaction.index = index_repository.resolve()
        transaction.directory = record_path.parent
        transaction.stage_directory = transaction.directory / "stage"
        transaction.backup_directory = transaction.directory / "backup"
        transaction.record_path = record_path
        transaction.intent_path = transaction.directory / "intent.jsonl"
        transaction.failure_injector = None
        transaction.record = TransactionRecord.model_validate(load_json(record_path))
        transaction.operations_by_path = {
            operation.relative_path: operation for operation in transaction.record.operations
        }
        transaction._replay_intents()
        transaction.run_id = transaction.record.run_id
        return transaction

    def _save_record(self) -> None:
        self.record.updated_at = utc_now()
        atomic_write_json(self.record_path, self.record.model_dump(mode="json"))

    def _append_intent(self, operation: TransactionOperation, stream: TextIO) -> None:
        payload = {
            "relative_path": operation.relative_path,
            "existed_before": operation.existed_before,
            "backup_relative_path": operation.backup_relative_path,
            "applied": True,
        }
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

    def _replay_intents(self) -> None:
        if not self.intent_path.exists():
            return
        operations = {operation.relative_path: operation for operation in self.record.operations}
        for line in self.intent_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            operation = operations.get(str(payload.get("relative_path", "")))
            if operation is None:
                raise ValueError("Transaction intent references an unknown operation")
            operation.existed_before = bool(payload.get("existed_before"))
            operation.backup_relative_path = str(payload.get("backup_relative_path", ""))
            operation.applied = bool(payload.get("applied"))

    def _relative(self, target: Path) -> str:
        resolved = target.resolve()
        if resolved != self.index and self.index not in resolved.parents:
            raise PermissionError(f"Transaction target escapes Index Repository: {target}")
        return resolved.relative_to(self.index).as_posix()

    def _operation(self, relative: str) -> TransactionOperation | None:
        return self.operations_by_path.get(relative)

    def write_text(self, target: Path, text: str, *, is_manifest: bool = False) -> None:
        relative = self._relative(target)
        staged = self.stage_directory / Path(relative)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text(text, encoding="utf-8", newline="\n")
        operation = self._operation(relative)
        staged_relative = staged.relative_to(self.directory).as_posix()
        if operation is None:
            operation = TransactionOperation(
                relative_path=relative,
                action="write",
                staged_relative_path=staged_relative,
                is_manifest=is_manifest,
            )
            self.record.operations.append(operation)
            self.operations_by_path[relative] = operation
        else:
            operation.action = "write"
            operation.staged_relative_path = staged_relative
            operation.is_manifest = operation.is_manifest or is_manifest

    def schedule_delete(self, target: Path) -> None:
        relative = self._relative(target)
        operation = self._operation(relative)
        if operation is None:
            operation = TransactionOperation(relative_path=relative, action="delete")
            self.record.operations.append(operation)
            self.operations_by_path[relative] = operation
        else:
            operation.action = "delete"
            operation.staged_relative_path = ""

    def staged_path_for(self, target: Path) -> Path | None:
        relative = self._relative(target)
        operation = self._operation(relative)
        if operation is None or operation.action != "write" or not operation.staged_relative_path:
            return None
        return self.directory / Path(operation.staged_relative_path)

    def _inject(self, phase: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(phase)

    def _atomic_copy_replace(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".txn", dir=target.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary)
        try:
            shutil.copy2(source, temporary_path)
            os.replace(temporary_path, target)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _apply(self, operation: TransactionOperation, intent_stream: TextIO) -> None:
        target = self.index / Path(operation.relative_path)
        self._inject(f"before:{operation.relative_path}")
        operation.existed_before = target.exists()
        if operation.existed_before:
            backup = self.backup_directory / Path(operation.relative_path)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            operation.backup_relative_path = backup.relative_to(self.directory).as_posix()
        # Persist rollback intent before touching the destination. Recovery can
        # then restore a backup (or remove a newly-created target) even if the
        # process dies inside os.replace/unlink.
        operation.applied = True
        self._append_intent(operation, intent_stream)
        if operation.action == "write":
            staged = self.directory / Path(operation.staged_relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, target)
        else:
            target.unlink(missing_ok=True)
        self._inject(f"after:{operation.relative_path}")

    def commit(self, manifest_path: Path, manifest_text: str) -> None:
        self.write_text(manifest_path, manifest_text, is_manifest=True)
        self.record.status = TransactionStatus.staged
        self._save_record()
        try:
            self.record.status = TransactionStatus.committing
            self._save_record()
            regular = [
                operation for operation in self.record.operations if not operation.is_manifest
            ]
            manifests = [operation for operation in self.record.operations if operation.is_manifest]
            if len(manifests) != 1:
                raise RuntimeError("A transaction must contain exactly one manifest operation")
            with self.intent_path.open("a", encoding="utf-8", newline="\n") as intent_stream:
                for operation in regular:
                    self._apply(operation, intent_stream)
                self._apply(manifests[0], intent_stream)
            self.record.manifest_committed = True
            self._save_record()
            self.intent_path.unlink(missing_ok=True)
            self._inject("after_manifest")
            self.record.status = TransactionStatus.recovery_required
            self._save_record()
        except Exception as error:
            self.record.error = f"{type(error).__name__}: {str(error)[:500]}"
            if self.record.manifest_committed:
                self.record.status = TransactionStatus.recovery_required
                self._save_record()
            else:
                self.failure_injector = None
                self.rollback()
            raise

    def rollback(self) -> None:
        for operation in reversed(self.record.operations):
            if not operation.applied:
                continue
            target = self.index / Path(operation.relative_path)
            if operation.existed_before and operation.backup_relative_path:
                backup = self.directory / Path(operation.backup_relative_path)
                self._atomic_copy_replace(backup, target)
            else:
                target.unlink(missing_ok=True)
            operation.applied = False
        self.record.status = TransactionStatus.rolled_back
        self.record.manifest_committed = False
        self._save_record()
        self.intent_path.unlink(missing_ok=True)
        shutil.rmtree(self.stage_directory, ignore_errors=True)
        shutil.rmtree(self.backup_directory, ignore_errors=True)


def recover_transactions(index_repository: Path) -> list[str]:
    index = index_repository.resolve()
    root = octopus_dir(index) / "transactions"
    if not root.exists():
        return []
    actions: list[str] = []
    for record_path in sorted(root.glob("*/record.json")):
        transaction = IndexTransaction.load(index, record_path)
        status = transaction.record.status
        if status in {TransactionStatus.committed, TransactionStatus.rolled_back}:
            continue
        if transaction.record.manifest_committed:
            actions.append(f"complete-derived:{transaction.run_id}")
        else:
            transaction.rollback()
            actions.append(f"rolled-back:{transaction.run_id}")
    return actions


def mark_transaction_complete(index_repository: Path, run_id: str) -> None:
    record_path = octopus_dir(index_repository.resolve()) / "transactions" / run_id / "record.json"
    transaction = IndexTransaction.load(index_repository, record_path)
    if not transaction.record.manifest_committed:
        raise RuntimeError("Cannot complete derived state before the manifest is committed")
    transaction.record.status = TransactionStatus.committed
    transaction._save_record()
    transaction.intent_path.unlink(missing_ok=True)
    shutil.rmtree(transaction.stage_directory, ignore_errors=True)
    shutil.rmtree(transaction.backup_directory, ignore_errors=True)


def run_report_directory(index_repository: Path) -> Path:
    return octopus_dir(index_repository.resolve()) / "runs"


def write_run_report(index_repository: Path, report: RunReport) -> Path:
    directory = run_report_directory(index_repository)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.run_id}.json"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(report.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def load_run_report(index_repository: Path, run_id: str | None = None) -> RunReport:
    directory = run_report_directory(index_repository)
    if run_id:
        path = directory / f"{run_id}.json"
    else:
        candidates = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            raise FileNotFoundError("No Octopus run reports are available")
        path = candidates[-1]
    return RunReport.model_validate(load_json(path))
