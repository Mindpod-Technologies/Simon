"""Observability: structured event log for monitoring and evals.

Every agent turn across every interface records one row in the ``events``
table (same SQLite database as memory). The monitoring portal
(:mod:`simon.monitor_app`) and the evals runner read from here.

Design: append-only, never raises — observability must never break a turn.
"""

from __future__ import annotations

import datetime
import json
import json as _json
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

-- Trace spans (OTel-lite): one trace per turn, a span per model call and
-- tool call, with token usage — the substrate for per-customer cost
-- accounting and drift analysis (2026-10-01).
CREATE TABLE IF NOT EXISTS spans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    parent TEXT DEFAULT '',
    name TEXT NOT NULL,           -- 'turn' | 'chat' | 'tool'
    session_id TEXT DEFAULT '',
    interface TEXT DEFAULT '',
    model TEXT DEFAULT '',
    duration_ms INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    detail TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_ts ON spans(ts);
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


def record_span(trace_id: str, name: str, *, parent: str = "",
                session_id: str = "", interface: str = "", model: str = "",
                duration_ms: int = 0, prompt_tokens: int = 0,
                completion_tokens: int = 0, detail: str = "",
                path: Optional[str] = None) -> None:
    """Append one trace span. Never raises — tracing must not break work."""
    try:
        conn = _connect(path)
        try:
            conn.execute(
                "INSERT INTO spans (ts, trace_id, parent, name, session_id,"
                " interface, model, duration_ms, prompt_tokens,"
                " completion_tokens, detail) VALUES (?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?)",
                (datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 trace_id, parent, name, session_id, interface, model,
                 duration_ms, prompt_tokens, completion_tokens, detail))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("obs.record_span failed: %s", exc)


def usage_by_day(days: int = 7, path: Optional[str] = None) -> list[dict]:
    """Token usage per model per day — the cost-accounting substrate."""
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT substr(ts, 1, 10) AS day, model,"
            " SUM(prompt_tokens) AS prompt_toks,"
            " SUM(completion_tokens) AS completion_toks, COUNT(*) AS calls"
            " FROM spans WHERE name = 'chat'"
            " AND ts >= date('now', ?)"
            " GROUP BY day, model ORDER BY day DESC",
            (f"-{int(days)} days",)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def activity(session_id: str, line: str, stage: str = "info",
             interface: str = "", path: Optional[str] = None) -> None:
    """One line for the live activity terminal (the WORK view's real-time
    feed of what Simon is doing: routing, tool calls, gate events)."""
    record_event("activity", session_id=session_id, interface=interface,
                 path=path, stage=stage, line=line)


def activity_since(after_id: int = 0, session_id: str = "",
                   limit: int = 200, path: Optional[str] = None) -> list[dict]:
    """Activity events with id > ``after_id``, oldest first — the terminal
    pane polls this incrementally."""
    conn = _connect(path)
    try:
        sql = ("SELECT id, ts, detail FROM events WHERE kind = 'activity'"
               " AND id > ?")
        args: list = [after_id]
        if session_id:
            sql += " AND session_id = ?"
            args.append(session_id)
        sql += " ORDER BY id ASC LIMIT ?"
        args.append(limit)
        rows = conn.execute(sql, tuple(args)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            detail = _json.loads(r["detail"]) if r["detail"] else {}
        except Exception:  # noqa: BLE001
            detail = {}
        inner = detail.get("detail", detail)
        if isinstance(inner, str):
            try:
                inner = _json.loads(inner)
            except Exception:  # noqa: BLE001
                inner = {}
        out.append({"id": r["id"], "ts": r["ts"],
                    "stage": inner.get("stage", "info"),
                    "line": inner.get("line", "")})
    return [e for e in out if e["line"]]


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
