"""Refuse worker handoffs that explicitly list unfinished work."""
from __future__ import annotations

import sqlite3
from typing import Optional


class EmptyCompletionError(ValueError):
    """``complete_task`` refused: no substantive ``result``, ``summary``, or
    stored result. A ``ValueError`` so tool error handlers treat it as
    recoverable. Review approvals are exempt (the human is the record)."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            f"completion blocked: {task_id} has no result or summary evidence"
        )


class IncompleteCompletionError(ValueError):
    """``complete_task`` refused: a worker's own handoff metadata lists work
    still outstanding (``remaining``/``unresolved``/...). Completing would close
    the card while the acceptance criteria are admittedly unmet, and ``done``
    is terminal for workers. A ``ValueError`` so tool error handlers treat it
    as recoverable; the card is not mutated."""

    def __init__(self, task_id: str, items: list[str]):
        self.task_id = task_id
        self.items = list(items)
        preview = "; ".join(self.items[:5])
        super().__init__(
            f"completion blocked: {task_id} handoff lists outstanding work: {preview}"
        )


# Handoff keys a worker uses to say "not finished". Non-empty values on a
# worker completion mean the card is not done.
_INCOMPLETE_METADATA_KEYS = ("remaining", "remaining_work", "unresolved", "outstanding")
_EMPTY_MARKERS = {"none", "n/a", "na", "nothing", "-", "[]"}


def _outstanding_items(metadata: Optional[dict]) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    items: list[str] = []
    for key in _INCOMPLETE_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            continue
        for item in value:
            text = str(item).strip() if item is not None else ""
            if text and text.lower() not in _EMPTY_MARKERS:
                items.append(text)
    return items


def _gate_incomplete_completion(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    metadata: Optional[dict],
    expected_run_id: Optional[int],
    force: bool,
) -> None:
    """Refuse a WORKER completion whose handoff admits outstanding work.

    Only worker completions (``expected_run_id`` set) are gated; operator
    ``force`` and manual CLI completions stay exempt. The worker should finish
    the work, ``kanban_request_review``, or ``kanban_block`` instead.
    """
    if force or expected_run_id is None:
        return
    items = _outstanding_items(metadata)
    if not items:
        return
    from hermes_cli import kanban_db as kb
    with kb.write_txn(conn):
        kb._append_event(
            conn, task_id, "completion_blocked_remaining_work",
            {"remaining": [kb._first_line(i, 200) for i in items[:20]]},
        )
    raise IncompleteCompletionError(task_id, items)


