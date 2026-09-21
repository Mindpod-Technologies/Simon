"""Support-case store: SQLite, one row per case, status lifecycle."""

from __future__ import annotations

import os
import sqlite3
import secrets
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS support_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_ref TEXT NOT NULL UNIQUE,        -- e.g. SC-8F3K2
    email TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',  -- open | in_progress | resolved
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_DB = os.path.join("data", "portal.db")


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    db = path or os.environ.get("PORTAL_DB", "") or DEFAULT_DB
    directory = os.path.dirname(os.path.abspath(db))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: Optional[str] = None) -> None:
    conn = _connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def create_case(email: str, plan: str, subject: str, body: str,
                path: Optional[str] = None) -> str:
    """File a case; returns its public reference (SC-XXXXX)."""
    subject, body = (subject or "").strip(), (body or "").strip()
    if len(subject) < 5 or len(body) < 10:
        raise ValueError("subject (5+ chars) and details (10+ chars) required")
    ref = "SC-" + secrets.token_hex(3).upper()
    init_db(path)
    conn = _connect(path)
    try:
        conn.execute(
            "INSERT INTO support_cases (case_ref, email, plan, subject, body)"
            " VALUES (?, ?, ?, ?, ?)", (ref, email, plan, subject, body))
        conn.commit()
    finally:
        conn.close()
    return ref


def list_cases(email: str, path: Optional[str] = None) -> list[dict]:
    """A customer's own cases, newest first."""
    init_db(path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT case_ref, subject, status, created_at, updated_at"
            " FROM support_cases WHERE email = ? ORDER BY id DESC",
            (email,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_case(case_ref: str, email: str, path: Optional[str] = None) -> Optional[dict]:
    """One case, scoped to its owner — nobody reads another's case."""
    init_db(path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM support_cases WHERE case_ref = ? AND email = ?",
            (case_ref, email)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_status(case_ref: str, status: str, path: Optional[str] = None) -> bool:
    """Support-side status change (in_progress | resolved)."""
    if status not in ("open", "in_progress", "resolved"):
        raise ValueError(f"bad status {status!r}")
    init_db(path)
    conn = _connect(path)
    try:
        cur = conn.execute(
            "UPDATE support_cases SET status = ?,"
            " updated_at = datetime('now') WHERE case_ref = ?",
            (status, case_ref))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
