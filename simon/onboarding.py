"""Onboarding moment: first contact → read the room → propose real work.

The Viktor move: within hours of joining, the AI employee comes back with
tailored ideas, not a generic greeting. Flow:

1. A new session's first message marks it onboarded (telegram_bot, slack).
2. The scheduler's onboarding job picks sessions old enough to have
   "settled" (≥ ONBOARDING_DELAY_MINUTES) that haven't received proposals.
3. An agent reads that person's recent messages + remembered facts + what
   Simon is already doing, and writes 3–5 SPECIFIC proposals — deliverable
   via the per-person notify channel.

Generic greeting once; tailored proposals forever after.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

ONBOARDING_DELAY_MINUTES = 120  # "I'll read up and come back with ideas"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS onboarded_sessions (
    session_id TEXT PRIMARY KEY,
    interface TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL DEFAULT (datetime('now')),
    proposals_sent INTEGER NOT NULL DEFAULT 0
);
"""

PROMPT = """You are Simon, an AI employee who just joined this person's workspace.
Below is what you know about them so far. Propose 3–5 SPECIFIC things you
could take on for them — grounded in their actual messages, facts, and
work patterns, never generic ("I can answer questions" is banned; each
idea must name a concrete recurring task or deliverable and why it fits
THEM). Format: one warm intro line, then numbered ideas, one line each,
then one closing line inviting them to say the word. Under 150 words.

WHAT YOU KNOW:
{context}
"""


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def mark(session_id: str, interface: str = "",
         path: Optional[str] = None) -> bool:
    """Record first contact. Idempotent — True only the first time."""
    if not session_id:
        return False
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO onboarded_sessions (session_id, interface)"
            " VALUES (?, ?)", (session_id, interface))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def due_for_proposals(path: Optional[str] = None) -> list[dict]:
    """Sessions settled long enough, proposals not yet sent."""
    init_db(path)
    conn = memory._connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM onboarded_sessions WHERE proposals_sent = 0"
            " AND first_seen <= datetime('now', ?)",
            (f"-{ONBOARDING_DELAY_MINUTES} minutes",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_proposed(session_id: str, path: Optional[str] = None) -> None:
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "UPDATE onboarded_sessions SET proposals_sent = 1"
            " WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


def build_context(session_id: str, limit: int = 12) -> str:
    """What Simon knows about this person: recent messages + facts."""
    parts: list[str] = []
    try:
        history = memory.get_history(session_id, limit=limit)
        if history:
            lines = [f"- {m['role']}: {str(m['content'])[:140]}"
                     for m in history]
            parts.append("Recent conversation:\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("onboarding context: history failed")
    try:
        facts = memory.list_facts()
        if facts:
            parts.append("Remembered facts:\n" + "\n".join(
                f"- {f['key']}: {str(f['value'])[:80]}" for f in facts[:10]))
    except Exception:  # noqa: BLE001
        logger.exception("onboarding context: facts failed")
    return "\n\n".join(parts) or "(Nothing yet — they just arrived.)"
