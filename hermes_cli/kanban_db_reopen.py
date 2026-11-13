"""Operator reopen of completed cards with descendant invalidation."""
from __future__ import annotations

import sqlite3
from typing import Optional


def reopen_done_task(
    conn: sqlite3.Connection, task_id: str, *, actor: str, reason: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Operator reopen ``done`` -> ``ready`` (``todo`` while a parent is open).

    For a card closed before its acceptance criteria were met. The terminal run
    history stays; the card gets a fresh claim, a reset failure breaker
    (deliberate operator action) and a ``reopened_done`` audit event. Every
    done/active descendant is re-gated through
    :func:`invalidate_descendants_for_parent_reopen`, same as a dashboard
    reopen. Returns ``(ok, reason)``.
    """
    from hermes_cli import kanban_db as kb

    cur_status = kb._task_status(conn, task_id)
    if cur_status is None:
        return False, f"task {task_id} not found"
    if cur_status != "done":
        return False, f"task {task_id} is {cur_status!r}; reopen only applies to 'done'"
    terminations: list[tuple[Optional[int], Optional[str], Optional[int]]] = []
    with kb.write_txn(conn):
        new_status = kb._landing_status_after_parents(conn, task_id)
        cur = conn.execute(
            "UPDATE tasks SET status = ?, completed_at = NULL, claim_lock = NULL, "
            "claim_expires = NULL, worker_pid = NULL, worker_started_at = NULL, "
            "current_run_id = NULL, consecutive_failures = 0 "
            "WHERE id = ? AND status = 'done'",
            (new_status, task_id),
        )
        if cur.rowcount != 1:
            return False, f"task {task_id} status changed during reopen"
        kb._append_event(conn, task_id, "reopened_done", {
            "actor": actor, "reason": reason, "status": new_status,
        })
        # Legacy event so live-feed consumers render the move.
        kb._append_event(conn, task_id, "status", {
            "status": new_status, "reason": "reopened_done", "previous_status": "done",
        })
        result = kb.invalidate_descendants_for_parent_reopen(conn, task_id, author=actor)
        terminations.extend(result["terminations"])
    for pid, claim_lock, started_at in terminations:
        kb._terminate_reclaimed_worker(pid, claim_lock, started_at=started_at)
    kb.recompute_ready(conn)
    return True, None


