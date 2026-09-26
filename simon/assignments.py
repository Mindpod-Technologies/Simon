"""Durable assignment state (Core 2.0, milestone 2).

The failure this fixes: Simon accepts a multi-step assignment ("research X,
design Y, email me the PRD"), the conversation runs long, the history budget
trims the original request out of the context window — and Simon quietly
forgets what he was asked to do, answering follow-ups as if no task existed.

Conversation history is trimmable; an assignment is a COMMITMENT. So the
active assignment lives in its own SQLite table (one open assignment per
session) and is injected into the system prompt on every turn, upstream of
history trimming. Receipts (tool names that actually ran) are recorded
against it, so "done" always means "done with evidence".

Lifecycle:
    open      — planner verdict marked the turn tool-mandatory + multi-step
    done      — the turn produced real tool receipts
    cancelled — the owner said never mind / forget it / cancel
    superseded— a new assignment replaced it

Shares the memory database (and its test path override) via memory._connect.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    receipts TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_assignments_session
    ON assignments(session_id, status);
"""

OPEN = "open"
DONE = "done"
CANCELLED = "cancelled"
SUPERSEDED = "superseded"


def init_db(path: Optional[str] = None) -> None:
    """Create the assignments table if it does not already exist."""
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def open_assignment(session_id: str, goal: str,
                    path: Optional[str] = None) -> int:
    """Open a new assignment for a session; supersedes any open one.

    Returns the new assignment id.
    """
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE assignments SET status = ?, updated_at = datetime('now') "
            "WHERE session_id = ? AND status = ?",
            (SUPERSEDED, session_id, OPEN))
        cur = conn.execute(
            "INSERT INTO assignments (session_id, goal) VALUES (?, ?)",
            (session_id, goal.strip()[:1000]))
        conn.commit()
        assignment_id = int(cur.lastrowid)
    finally:
        conn.close()
    logger.info("assignment #%d opened for session %s: %.80s",
                assignment_id, session_id, goal)
    return assignment_id


def active(session_id: str, path: Optional[str] = None) -> Optional[dict]:
    """Return the session's open assignment, or None."""
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT id, session_id, goal, status, receipts, created_at, "
            "updated_at FROM assignments WHERE session_id = ? AND status = ? "
            "ORDER BY id DESC LIMIT 1",
            (session_id, OPEN)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    result = dict(row)
    try:
        result["receipts"] = json.loads(result.get("receipts") or "[]")
    except (TypeError, ValueError):
        result["receipts"] = []
    return result


def record_receipts(assignment_id: int, tools: list[str],
                    path: Optional[str] = None) -> None:
    """Append tool receipts to an assignment's evidence trail."""
    if not tools:
        return
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT receipts FROM assignments WHERE id = ?",
            (assignment_id,)).fetchone()
        existing: list[str] = []
        if row is not None:
            try:
                existing = json.loads(row["receipts"] or "[]")
            except (TypeError, ValueError):
                existing = []
        existing.extend(tools)
        conn.execute(
            "UPDATE assignments SET receipts = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (json.dumps(existing), assignment_id))
        conn.commit()
    finally:
        conn.close()


def close(assignment_id: int, status: str = DONE,
          path: Optional[str] = None) -> None:
    """Close an assignment with a terminal status."""
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE assignments SET status = ?, updated_at = datetime('now') "
            "WHERE id = ?", (status, assignment_id))
        conn.commit()
    finally:
        conn.close()
    logger.info("assignment #%d closed as %s", assignment_id, status)


def render_active(session_id: str, path: Optional[str] = None) -> str:
    """Render the standing assignment block for the system prompt.

    Injected on every turn so context trimming can never amputate the
    commitment. Empty string when nothing is open.
    """
    current = active(session_id, path)
    if current is None:
        return ""
    receipts = current["receipts"]
    evidence = (", ".join(dict.fromkeys(receipts)) if receipts
                else "none yet")
    return (
        "\n\nACTIVE ASSIGNMENT (standing commitment — this survives context "
        "trimming and overrides any earlier conversation you can no longer "
        "see):\n"
        f"Goal: {current['goal']}\n"
        f"Tool receipts so far: {evidence}\n"
        "This assignment is OPEN. Work it with real tool calls until it is "
        "genuinely complete; never claim it is done without receipts, and "
        "treat follow-up messages about it as part of the same task.")
