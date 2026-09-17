"""Per-person profiles: who is speaking, per canonical session.

The identity map (SIMON_IDENTITY_MAP) decides WHICH session a person lands
in; profiles decide what Simon calls them and what he knows about them.
Names are auto-captured from Telegram display names on first contact, or
set by hand via /api/profiles. Sessions without a profile are the owner —
the JARVIS "sir" stays his alone.
"""

from __future__ import annotations

from typing import Optional

from . import memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    session_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    form TEXT NOT NULL DEFAULT '',      -- optional: 'ma'am', 'Dr.', …
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_profile(session_id: str, path: Optional[str] = None) -> Optional[dict]:
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT * FROM profiles WHERE session_id = ?",
            (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_profile(session_id: str, name: str, form: str = "",
                path: Optional[str] = None) -> None:
    """Set or overwrite a profile's display name/form of address."""
    name = (name or "").strip()
    if not session_id or not name:
        return
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "INSERT INTO profiles (session_id, name, form, updated_at)"
            " VALUES (?, ?, ?, datetime('now'))"
            " ON CONFLICT(session_id) DO UPDATE SET"
            " name = excluded.name,"
            " form = CASE WHEN excluded.form != '' THEN excluded.form"
            "             ELSE profiles.form END,"
            " updated_at = datetime('now')",
            (session_id, name, form or ""))
        conn.commit()
    finally:
        conn.close()


def ensure_name(session_id: str, name: str, path: Optional[str] = None) -> bool:
    """Auto-capture: fill the name ONLY if none exists yet. A name the
    person (or owner) chose deliberately always wins over a chat handle.
    Returns True if the name was newly captured."""
    if get_profile(session_id, path=path):
        return False
    before = get_profile(session_id, path=path)
    if before:
        return False
    set_profile(session_id, name, path=path)
    return True


def display_name(session_id: str, path: Optional[str] = None) -> str:
    """The person's name, or '' when the session has no profile (owner)."""
    prof = get_profile(session_id, path=path)
    return (prof or {}).get("name", "")
