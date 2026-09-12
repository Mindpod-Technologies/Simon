"""Observability: structured event log for monitoring and evals.

Every agent turn across every interface records one row in the ``events``
table (same SQLite database as memory). The monitoring portal
(:mod:`simon.monitor_app`) and the evals runner read from here.

Design: append-only, never raises — observability must never break a turn.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from typing import Any, Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,           -- 'turn' | 'error' | 'eval'
    interface TEXT DEFAULT '',
    session_id TEXT DEFAULT '',
    model TEXT DEFAULT '',
    route_reason TEXT DEFAULT '',
    latency_ms INTEGER DEFAULT 0,
    detail TEXT DEFAULT ''        -- JSON: tools used, reply length, extras
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


def _connect(path: Optional[str] = None) -> sqlite3.Connection:
    from simon import memory
    conn = memory._connect(path)
    conn.executescript(_SCHEMA)
    return conn


def record_event(kind: str, *, interface: str = "", session_id: str = "",
                 model: str = "", route_reason: str = "",
                 latency_ms: int = 0, path: Optional[str] = None,
                 **detail: Any) -> None:
    """Append one event row. Never raises."""
    try:
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT INTO events (ts, kind, interface, session_id, model,"
                " route_reason, latency_ms, detail) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 kind, interface, session_id, model, route_reason,
                 int(latency_ms), json.dumps(detail, default=str)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - observability must not break turns
        log.warning("obs.record_event failed: %s", exc)


def recent_events(limit: int = 50, kind: Optional[str] = None,
                  path: Optional[str] = None) -> list[dict]:
    conn = _connect(path)
    try:
        sql = "SELECT * FROM events"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (kind,)
        sql += " ORDER BY id DESC LIMIT ?"
        args += (limit,)
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def summary(path: Optional[str] = None) -> dict:
    """Aggregate stats for the monitoring dashboard."""
    conn = _connect(path)
    try:
        def q(sql: str, args: tuple = ()) -> list:
            return conn.execute(sql, args).fetchall()

        turns = q("SELECT COUNT(*) c, AVG(latency_ms) avg_lat FROM events"
                  " WHERE kind='turn'")[0]
        by_interface = {r["interface"] or "?": r["c"] for r in q(
            "SELECT interface, COUNT(*) c FROM events WHERE kind='turn'"
            " GROUP BY interface ORDER BY c DESC")}
        by_model = {r["model"] or "?": r["c"] for r in q(
            "SELECT model, COUNT(*) c FROM events WHERE kind='turn'"
            " GROUP BY model ORDER BY c DESC")}
        latencies = [r["latency_ms"] for r in q(
            "SELECT latency_ms FROM events WHERE kind='turn'"
            " ORDER BY id DESC LIMIT 200")]
        errors = q("SELECT COUNT(*) c FROM events WHERE kind='error'")[0]["c"]
        last_eval = q("SELECT ts, detail FROM events WHERE kind='eval'"
                      " ORDER BY id DESC LIMIT 1")
    finally:
        conn.close()

    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    return {
        "total_turns": turns["c"] or 0,
        "avg_latency_ms": int(turns["avg_lat"] or 0),
        "p95_latency_ms": p95,
        "by_interface": by_interface,
        "by_model": by_model,
        "error_count": errors,
        "last_eval": (json.loads(last_eval[0]["detail"])
                      if last_eval else None),
        "last_eval_ts": last_eval[0]["ts"] if last_eval else None,
    }
