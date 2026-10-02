"""SQLite-backed persistent memory for Simon.

Stores conversation history, long-term facts, and scheduled reminders in a
single SQLite database (default ``./data/simon.db``). The data directory is
created automatically. All functions take an optional ``path`` override so
tests can use a temporary database.
"""

from __future__ import annotations

import json
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
        # Provenance migration (2026-09-28): every fact carries its source
        # ('user' = stated by a human, 'model' = inferred by Simon) and a
        # quarantine flag. Two production incidents were memory poisoning —
        # model-written fabrications injected as ground truth for weeks.
        # Temporal migration (2026-10-01): valid_until (None = forever),
        # reinforced (confirmation count), last_confirmed — trust decays
        # for model-inferred facts that nobody ever re-confirms.
        for ddl in (
            "ALTER TABLE facts ADD COLUMN source TEXT NOT NULL"
            " DEFAULT 'user'",
            "ALTER TABLE facts ADD COLUMN quarantined INTEGER NOT NULL"
            " DEFAULT 0",
            "ALTER TABLE facts ADD COLUMN valid_until TEXT",
            "ALTER TABLE facts ADD COLUMN reinforced INTEGER NOT NULL"
            " DEFAULT 1",
            "ALTER TABLE facts ADD COLUMN last_confirmed TEXT",
        ):
            try:
                conn.execute(ddl)
                conn.commit()
            except Exception:  # column already exists
                pass
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


def set_fact(key: str, value: str, path: Optional[str] = None,
             source: str = "user", valid_until: Optional[str] = None) -> None:
    """Insert or update a long-term fact, tagged with its provenance.

    Temporal trust: re-stating the same value REINFORCES the record
    (reinforced++, last_confirmed=now); a new value supersedes with
    reinforce reset. ``valid_until`` (ISO date/datetime) makes the fact
    self-expiring — it stops surfacing after that moment.
    """
    if source not in ("user", "model"):
        source = "model"
    conn = _connect(path)
    try:
        existing = None
        try:
            existing = conn.execute(
                "SELECT value, reinforced FROM facts WHERE key = ?",
                (key,)).fetchone()
        except Exception:  # pre-migration DB
            pass
        if existing is not None and existing["value"] == value:
            conn.execute(
                "UPDATE facts SET reinforced = reinforced + 1,"
                " last_confirmed = datetime('now'),"
                " updated_at = datetime('now') WHERE key = ?", (key,))
        elif existing is not None:
            conn.execute(
                "UPDATE facts SET value = ?, source = ?, reinforced = 1,"
                " last_confirmed = datetime('now'), valid_until = ?,"
                " updated_at = datetime('now') WHERE key = ?",
                (value, source, valid_until, key))
        else:
            conn.execute(
                "INSERT INTO facts (key, value, source, valid_until,"
                " reinforced, last_confirmed, updated_at)"
                " VALUES (?, ?, ?, ?, 1, datetime('now'), datetime('now'))",
                (key, value, source, valid_until))
        conn.commit()
    except Exception:  # pre-migration DB without the new columns
        conn.execute(
            "INSERT INTO facts (key, value, source, updated_at)"
            " VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "source = excluded.source, updated_at = excluded.updated_at",
            (key, value, source))
        conn.commit()
    finally:
        conn.close()


def quarantine_fact(key: str, path: Optional[str] = None) -> bool:
    """Quarantine a fact: kept for audit, excluded from recall."""
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE facts SET quarantined = 1 WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def unquarantine_fact(key: str, path: Optional[str] = None) -> bool:
    """Lift a quarantine (the fact was verified after all)."""
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE facts SET quarantined = 0 WHERE key = ?", (key,))
        conn.commit()
        return cur.rowcount > 0
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


def search_facts(query: str, path: Optional[str] = None,
                 detailed: bool = False) -> list:
    """Return ``(key, value)`` pairs relevant to ``query``.

    Quarantined facts are excluded. User-sourced facts outrank
    model-inferred ones at equal word score — a human's statement is
    stronger evidence than the model's inference. ``detailed=True`` returns
    ``(key, value, source)`` triples so the caller can label unverified
    entries.

    Word-based matching: the query is split into significant words
    (stopwords removed), and each fact's key+value (underscores treated as
    spaces) is scored by how many of those words it contains. Facts matching
    every significant word rank first, then partial matches. Falls back to
    substring matching when the query has no significant words.
    """
    significant = significant_words(query)

    conn = _connect(path)
    try:
        try:
            rows = conn.execute(
                "SELECT key, value, source, reinforced FROM facts"
                " WHERE quarantined = 0"
                " AND (valid_until IS NULL OR valid_until > datetime('now'))"
                ).fetchall()
        except Exception:  # pre-migration database
            rows = conn.execute("SELECT key, value FROM facts").fetchall()
    finally:
        conn.close()

    def _pack(row):
        source = row["source"] if "source" in row.keys() else "user"
        return ((row["key"], row["value"], source) if detailed
                else (row["key"], row["value"]))

    if not significant:
        like = (query or "").lower()
        return [_pack(r) for r in rows
                if like in r["key"].lower() or like in r["value"].lower()]

    scored: list[tuple[int, int, int, Any]] = []
    for row in rows:
        haystack = f"{row['key'].replace('_', ' ')} {row['value']}".lower()
        score = sum(1 for w in significant if w in haystack)
        if score:
            source = row["source"] if "source" in row.keys() else "user"
            trust = 0 if source == "user" else 1
            reinforced = (row["reinforced"]
                          if "reinforced" in row.keys() else 1)
            scored.append((score, trust, -int(reinforced or 1), row))
    scored.sort(key=lambda item: (-item[0], item[1], item[2],
                                  item[3]["key"]))
    return [_pack(row) for _score, _trust, _neg_reinf, row in scored]


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


# ---------------------------------------------------------------------------
# Erasure cascade (GDPR-grade "delete everything about X")
# ---------------------------------------------------------------------------

_ERASURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS erasure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term_hash TEXT NOT NULL,
    counts TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def erase_subject(term: str, include_history: bool = False,
                  path: Optional[str] = None) -> dict:
    """Erase a data subject term across every store, with a tombstone.

    - facts: matching (key or value) are QUARANTINED (recoverable) —
      hard-delete is available via delete_fact for confirmed cases
    - RAG: matching chunks are deleted from rag_chunks AND the FTS mirror
    - history: with include_history=True, matching messages are DELETED
      (off by default — conversation records are legal-hold material)
    - tombstone: an erasure_log row (term stored HASHED, never plain)
      recording counts — provable deletion without retaining the data.

    Returns the per-store counts.
    """
    import hashlib

    init_db(path)
    term_hash = hashlib.sha256(term.strip().lower().encode()).hexdigest()
    like = f"%{term.strip()}%"
    counts: dict[str, int] = {}
    conn = _connect(path)
    try:
        conn.executescript(_ERASURE_SCHEMA)
        cur = conn.execute(
            "UPDATE facts SET quarantined = 1 WHERE key LIKE ? OR value"
            " LIKE ?", (like, like))
        counts["facts_quarantined"] = cur.rowcount
        for table in ("rag_chunks", "rag_fts"):
            try:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE content LIKE ?", (like,))
                counts[f"{table}_deleted"] = cur.rowcount
            except Exception:  # table absent
                counts[f"{table}_deleted"] = 0
        if include_history:
            cur = conn.execute(
                "DELETE FROM messages WHERE content LIKE ?", (like,))
            counts["messages_deleted"] = cur.rowcount
        else:
            counts["messages_deleted"] = 0
        conn.execute(
            "INSERT INTO erasure_log (term_hash, counts) VALUES (?, ?)",
            (term_hash, json.dumps(counts)))
        conn.commit()
    finally:
        conn.close()
    return counts


def erasure_log(path: Optional[str] = None) -> list[dict]:
    """The tombstone ledger — provable deletion without retained data."""
    conn = _connect(path)
    try:
        conn.executescript(_ERASURE_SCHEMA)
        rows = conn.execute(
            "SELECT term_hash, counts, created_at FROM erasure_log"
            " ORDER BY id DESC").fetchall()
        return [{"term_hash": r["term_hash"], "counts": r["counts"],
                 "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()
