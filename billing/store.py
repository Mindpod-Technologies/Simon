"""Issued-license store: SQLite, idempotent on the Stripe session id."""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issued_licenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stripe_session TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL,
    plan TEXT NOT NULL,
    license_key TEXT NOT NULL,
    emailed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_DB = os.path.join("data", "billing.db")


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    db = path or os.environ.get("BILLING_DB", "") or DEFAULT_DB
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


def record_issued(stripe_session: str, email: str, plan: str,
                  license_key: str, path: Optional[str] = None) -> bool:
    """Store an issued key. False if this session was already fulfilled
    (idempotent — Stripe retries webhooks)."""
    init_db(path)
    conn = _connect(path)
    try:
        try:
            conn.execute(
                "INSERT INTO issued_licenses"
                " (stripe_session, email, plan, license_key)"
                " VALUES (?, ?, ?, ?)",
                (stripe_session, email, plan, license_key))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    finally:
        conn.close()


def mark_emailed(stripe_session: str, path: Optional[str] = None) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            "UPDATE issued_licenses SET emailed = 1 WHERE stripe_session = ?",
            (stripe_session,))
        conn.commit()
    finally:
        conn.close()


def lookup(stripe_session: str, path: Optional[str] = None) -> Optional[dict]:
    init_db(path)
    conn = _connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM issued_licenses WHERE stripe_session = ?",
            (stripe_session,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_issued(limit: int = 50, path: Optional[str] = None) -> list[dict]:
    init_db(path)
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT id, stripe_session, email, plan, emailed, created_at"
            " FROM issued_licenses ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
