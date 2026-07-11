from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from octopus.config import load_repository_state
from octopus.models import NodeState
from octopus.scanner import RepositoryScanner


def test_office_lock_defers_target(repository: tuple[Path, Path, object]) -> None:
    raw, index, config = repository
    (raw / "论文.docx").write_bytes(b"not parsed while locked")
    (raw / "~$论文.docx").write_bytes(b"lock")
    state = load_repository_state(index, config)
    state, outcome = RepositoryScanner(config).scan(state, force_path="*")
    target = next(node for node in state.nodes.values() if node.raw_relative_path == "论文.docx")
    assert target.state == NodeState.pending_edit
    assert target.stability.editing_signals == ["office_temporary_lock"]
    assert outcome.ignored == 1
    assert target.node_id in state.queues.pending_edit


def test_move_reuses_stable_node_id(repository: tuple[Path, Path, object]) -> None:
    raw, index, config = repository
    original = raw / "old.pdf"
    original.write_bytes(b"same-content")
    state = load_repository_state(index, config)
    state, _ = RepositoryScanner(config).scan(state, force_path="*")
    old_node = next(node for node in state.nodes.values() if node.raw_relative_path == "old.pdf")
    original.rename(raw / "new.pdf")
    state, outcome = RepositoryScanner(config).scan(state, force_path="*")
    new_node = next(node for node in state.nodes.values() if node.raw_relative_path == "new.pdf")
    assert new_node.node_id == old_node.node_id
    assert new_node.state == NodeState.moved
    assert outcome.moved == 1


def test_long_running_office_lock_becomes_stale(repository: tuple[Path, Path, object]) -> None:
    raw, index, config = repository
    (raw / "locked.xlsx").write_bytes(b"workbook")
    (raw / "~$locked.xlsx").write_bytes(b"lock")
    state = load_repository_state(index, config)
    state, _ = RepositoryScanner(config).scan(state, force_path="*")
    node = next(item for item in state.nodes.values() if item.raw_relative_path == "locked.xlsx")
    node.stability.pending_deadline_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state, _ = RepositoryScanner(config).scan(state, force_path="*")
    node = next(item for item in state.nodes.values() if item.raw_relative_path == "locked.xlsx")
    assert node.state == NodeState.stale
    assert node.node_id not in state.queues.pending_edit
