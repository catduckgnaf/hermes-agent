"""Worker completions whose handoff lists outstanding work are refused."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    db_path = kb.kanban_db_path(board="default")
    kb._INITIALIZED_PATHS.discard(str(db_path.resolve()))
    kb.init_db()
    with kbc.connect() as c:
        yield c


def _running(conn, title="demo") -> tuple[str, int]:
    tid = kb.create_task(conn, title=title, assignee="coder")
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
    return tid, kb.get_task(conn, tid).current_run_id


def _kinds(conn, tid):
    return [r["kind"] for r in conn.execute(
        "SELECT kind FROM task_events WHERE task_id = ? ORDER BY id", (tid,))]


def test_worker_completion_with_remaining_items_is_refused(conn):
    tid, run_id = _running(conn)
    with pytest.raises(kb.IncompleteCompletionError) as err:
        kb.complete_task(
            conn, tid, summary="PRs open", expected_run_id=run_id,
            metadata={"remaining": ["CI pending", "No PRs merged"]},
        )
    assert "CI pending" in str(err.value)
    assert kb.get_task(conn, tid).status == "running"
    kinds = _kinds(conn, tid)
    assert "completion_blocked_remaining_work" in kinds
    assert "completed" not in kinds


@pytest.mark.parametrize("meta", [
    {"remaining": []}, {"remaining": ["none"]}, {"unresolved": "n/a"}, {"changed_files": ["a"]}, None,
])
def test_empty_remaining_markers_allow_completion(conn, meta):
    tid, run_id = _running(conn)
    assert kb.complete_task(conn, tid, summary="all done", expected_run_id=run_id, metadata=meta)
    assert kb.get_task(conn, tid).status == "done"


@pytest.mark.parametrize("key", ["unresolved", "outstanding", "remaining_work"])
def test_alternate_keys_are_gated(conn, key):
    tid, run_id = _running(conn)
    with pytest.raises(kb.IncompleteCompletionError):
        kb.complete_task(conn, tid, summary="x", expected_run_id=run_id, metadata={key: "deploy unverified"})


def test_operator_force_and_manual_completion_are_exempt(conn):
    tid, _ = _running(conn)
    assert kb.complete_task(conn, tid, summary="operator closes", force=True,
                            metadata={"remaining": ["follow-up elsewhere"]})
    tid2 = kb.create_task(conn, title="manual", assignee="coder")
    assert kb.complete_task(conn, tid2, summary="manual", metadata={"remaining": ["x"]})
