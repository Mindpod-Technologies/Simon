"""Idempotency for mutating tool calls.

The failure this prevents: the completion gate (or a nudge loop, or a
refired turn) re-executes a mutating call that ALREADY ran — a customer
gets two identical emails, a channel gets double-posted. Agent frameworks
learned this from payments: mutating calls carry a derived key, and a
repeat within the TTL returns the recorded result instead of running again.

Key = sha256(tool | canonical-args). Repeat within TTL → recorded result +
a "(not repeated)" note. Errors are never cached. Tools own idempotency,
not the agent (Stripe/IETF pattern, adapted).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idem_keys (
    key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

_TTL_S = 600  # 10 minutes — covers gate retries and turn refires


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def key_for(tool: str, args: dict) -> str:
    canonical = json.dumps(args or {}, sort_keys=True, default=str)
    return hashlib.sha256(f"{tool}|{canonical}".encode()).hexdigest()[:32]


def lookup(key: str, path: Optional[str] = None,
           ttl_s: float = _TTL_S) -> Optional[str]:
    """The recorded result if this exact call completed within the TTL."""
    init_db(path)
    conn = memory._connect(path)
    try:
        row = conn.execute(
            "SELECT result, created_at FROM idem_keys WHERE key = ?",
            (key,)).fetchone()
    finally:
        conn.close()
    if row is None or (time.time() - row["created_at"]) > ttl_s:
        return None
    return row["result"]


def record(key: str, tool: str, result: str, path: Optional[str] = None) -> None:
    """Record a successful mutating execution. Errors are never cached."""
    if str(result).startswith("Error"):
        return
    init_db(path)
    conn = memory._connect(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO idem_keys (key, tool, result, created_at)"
            " VALUES (?, ?, ?, ?)",
            (key, tool, str(result), time.time()))
        conn.commit()
    finally:
        conn.close()
