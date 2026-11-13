"""Operators can reopen a done card; run history is kept and descendants are re-gated."""

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


def test_reopen_done_returns_card_to_ready_with_audit(conn):
    tid, run_id = _running(conn)
    assert kb.complete_task(conn, tid, summary="closed early", expected_run_id=run_id)
    ok, detail = kb.reopen_done_task(conn, tid, actor="operator", reason="PRs not merged")
    assert ok, detail
    task = kb.get_task(conn, tid)
    assert task.status == "ready"
    assert task.completed_at is None
    assert task.current_run_id is None
    assert "reopened_done" in _kinds(conn, tid)
    # Run history is preserved and the card can be claimed again.
    assert len(kb.list_runs(conn, tid)) == 1
    assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None


def test_reopen_refuses_non_done(conn):
    tid, _ = _running(conn)
    ok, detail = kb.reopen_done_task(conn, tid, actor="operator", reason="x")
    assert not ok and "only applies to 'done'" in detail
    assert kb.get_task(conn, tid).status == "running"


def test_reopen_regates_done_children(conn):
    parent, prun = _running(conn, "parent")
    child = kb.create_task(conn, title="child", assignee="coder", parents=[parent])
    assert kb.complete_task(conn, parent, summary="p", expected_run_id=prun)
    kb.recompute_ready(conn)
    assert kb.claim_task(conn, child, claimer=kb._claimer_id()) is not None
    crun = kb.get_task(conn, child).current_run_id
    assert kb.complete_task(conn, child, summary="c", expected_run_id=crun)
    ok, _ = kb.reopen_done_task(conn, parent, actor="operator", reason="redo")
    assert ok
    assert kb.get_task(conn, parent).status == "ready"
    assert kb.get_task(conn, child).status == "todo"

