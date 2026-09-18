"""SQLite-backed persistent memory for Simon.

Stores conversation history, long-term facts, and scheduled reminders in a
single SQLite database (default ``./data/simon.db``). The data directory is
created automatically. All functions take an optional ``path`` override so
tests can use a temporary database.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

DEFAULT_DB_PATH = os.path.join(".", "data", "simon.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    run_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reminders_run_at ON reminders(run_at);
"""


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open a connection, ensuring the parent directory exists."""
    db_path = path or DEFAULT_DB_PATH
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Optional[str] = None) -> None:
    """Create the database schema if it does not already exist."""
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def add_message(session_id: str, role: str, content: str,
                path: Optional[str] = None) -> None:
    """Append a message to a conversation session."""
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content),
        )
        conn.commit()
    finally:
        conn.close()


def get_history(session_id: str, limit: int = 40,
                path: Optional[str] = None) -> list[dict]:
    """Return the most recent ``limit`` messages for a session, oldest first.

    Each item is ``{"role": ..., "content": ...}``.
    """
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [{"role": row["role"], "content": row["content"]}
            for row in reversed(rows)]


def set_fact(key: str, value: str, path: Optional[str] = None) -> None:
    """Insert or update a long-term fact."""
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_fact(key: str, path: Optional[str] = None) -> Optional[str]:
    """Return the value stored for ``key``, or ``None`` if absent."""
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM facts WHERE key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    return row["value"] if row else None


def list_facts(path: Optional[str] = None) -> list[dict]:
    """All long-term facts, newest first — for the Settings memory UI."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM facts "
            "ORDER BY updated_at DESC").fetchall()
    finally:
        conn.close()
    return [{"key": r["key"], "value": r["value"],
             "updated_at": r["updated_at"]} for r in rows]


def delete_fact(key: str, path: Optional[str] = None) -> bool:
    """Delete a fact by key. Returns True when one was removed."""
    conn = _connect(path)
    try:
        cur = conn.execute("DELETE FROM facts WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


_QUERY_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "on", "of", "my", "our", "your",
    "what", "what's", "whats", "where", "when", "who", "which", "how",
    "do", "does", "did", "it", "to", "in", "for", "me", "i", "we", "and",
    "please", "tell", "know", "about", "simon",
}


def significant_words(text: str) -> list[str]:
    """Tokenise text into meaningful query words (stopwords removed)."""
    words = [w.strip("?.,!\"'").lower() for w in (text or "").split()]
    return [w for w in words if w and w not in _QUERY_STOPWORDS]


def search_facts(query: str, path: Optional[str] = None) -> list[tuple[str, str]]:
    """Return ``(key, value)`` pairs relevant to ``query``.

    Word-based matching: the query is split into significant words
    (stopwords removed), and each fact's key+value (underscores treated as
    spaces) is scored by how many of those words it contains. Facts matching
    every significant word rank first, then partial matches. Falls back to
    substring matching when the query has no significant words.
    """
    significant = significant_words(query)

    conn = _connect(path)
    try:
        rows = conn.execute("SELECT key, value FROM facts").fetchall()
    finally:
        conn.close()

    if not significant:
        like = (query or "").lower()
        return [(r["key"], r["value"]) for r in rows
                if like in r["key"].lower() or like in r["value"].lower()]

    scored: list[tuple[int, str, str]] = []
    for row in rows:
        haystack = f"{row['key'].replace('_', ' ')} {row['value']}".lower()
        score = sum(1 for w in significant if w in haystack)
        if score:
            scored.append((score, row["key"], row["value"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(key, value) for _score, key, value in scored]


def add_reminder(text: str, run_at_iso: str, session_id: str = "",
                 path: Optional[str] = None) -> int:
    """Schedule a reminder for ``run_at_iso`` (ISO-8601). Returns its id.
    ``session_id`` attributes it to the person who asked (notification
    routing); empty = owner."""
    _ensure_reminder_session_col(path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO reminders (text, run_at, session_id)"
            " VALUES (?, ?, ?)",
            (text, run_at_iso, session_id or ""),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _ensure_reminder_session_col(path: Optional[str] = None) -> None:
    """Migration: attribution column on reminders (added 2026-09).
    Idempotent — the ALTER is simply swallowed when the column exists."""
    conn = _connect(path)
    try:
        try:
            conn.execute(
                "ALTER TABLE reminders ADD COLUMN session_id TEXT"
                " NOT NULL DEFAULT ''")
            conn.commit()
        except Exception:  # column already exists
            pass
    finally:
        conn.close()


def due_reminders(now_iso: str, path: Optional[str] = None) -> list[dict]:
    """Return reminders due at or before ``now_iso``, oldest first.

    Each item is ``{"id", "text", "run_at", "session_id"}``.
    """
    _ensure_reminder_session_col(path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT id, text, run_at, session_id FROM reminders"
            " WHERE run_at <= ? ORDER BY run_at",
            (now_iso,),
        ).fetchall()
    finally:
        conn.close()
    return [{"id": row["id"], "text": row["text"], "run_at": row["run_at"],
             "session_id": row["session_id"]}
            for row in rows]


def delete_reminder(reminder_id: int, path: Optional[str] = None) -> None:
    """Delete a reminder by id (no-op if it does not exist)."""
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
    finally:
        conn.close()
