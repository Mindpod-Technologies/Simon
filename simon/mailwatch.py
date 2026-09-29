"""Deterministic mail-watch state: which inbox messages were already
reported to the owner.

The old design asked the MODEL to dedupe against its own conversation
history — history trimming loses it, so the same "unread weekly digest" was
re-briefed 19 times in a week (2026-09-26 sweep), and question-shaped
replies ("How can I help you with these?") leaked to the owner as mail
alerts. New mail is a data question, not a judgment call: the message id is
either in this table or it isn't.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reported_mail (
    id TEXT PRIMARY KEY,
    reported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_ID_RE = re.compile(r"^\s*id:\s*(\S+)\s*$", re.MULTILINE)


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def parse_ids(tool_output: str) -> list[str]:
    """Graph message ids from read_recent_emails output."""
    return _ID_RE.findall(str(tool_output or ""))


def unseen(ids: list[str], path: Optional[str] = None) -> list[str]:
    """The ids that have never been reported."""
    if not ids:
        return []
    init_db(path)
    conn = memory._connect(path)
    try:
        marks = ",".join("?" for _ in ids)
        seen = {r["id"] for r in conn.execute(
            f"SELECT id FROM reported_mail WHERE id IN ({marks})",
            ids).fetchall()}
    finally:
        conn.close()
    return [i for i in ids if i not in seen]


def mark_reported(ids: list[str], path: Optional[str] = None) -> None:
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO reported_mail (id) VALUES (?)",
            [(i,) for i in ids])
        conn.commit()
    finally:
        conn.close()


def blocks_for_ids(tool_output: str, ids: list[str]) -> str:
    """Extract just the message blocks for the given ids, in order."""
    blocks = re.split(r"\n\s*\n", str(tool_output or ""))
    wanted = set(ids)
    picked = [b for b in blocks
              if (m := _ID_RE.search(b)) and m.group(1) in wanted]
    return "\n\n".join(picked)
