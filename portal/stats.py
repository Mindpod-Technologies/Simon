"""Per-tenant stats: read a hosted tenant's obs DB (same host) read-only.

Self-host customers' data never reaches us — no phone-home — so the
dashboard for them shows license status only. Hosted tenants get the live
operational picture: activity, model routing, latency, jobs, automations,
approvals, eval trend.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional


def _tenant_db(tenant_dir: str) -> Optional[Path]:
    db = Path(tenant_dir) / "data" / "simon.db"
    return db if db.is_file() else None


def tenant_stats(tenant_dir: str, days: int = 7) -> dict:
    """Aggregate a tenant's last ``days`` of activity. Never raises — a
    missing/old DB yields an honest empty dashboard."""
    db = _tenant_db(tenant_dir)
    empty = {"hosted": False}
    if db is None:
        return empty
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return _gather(conn, days)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - dashboard must never 500
        return empty


def _gather(conn: sqlite3.Connection, days: int) -> dict:
    since = f"-{days} days"
    out: dict = {"hosted": True, "days": days}

    def q(sql, args=(since,)):
        try:
            return conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    turns = q("SELECT interface, COUNT(*) c FROM events"
              " WHERE kind = 'turn' AND ts >= datetime('now', ?)"
              " GROUP BY interface")
    out["turns_total"] = sum(r["c"] for r in turns)
    out["turns_by_channel"] = {r["interface"] or "web": r["c"] for r in turns}

    routes = q("SELECT route_reason, COUNT(*) c FROM events"
               " WHERE kind = 'turn' AND ts >= datetime('now', ?)"
               " GROUP BY route_reason ORDER BY c DESC LIMIT 8")
    out["top_routes"] = {r["route_reason"] or "default": r["c"]
                         for r in routes}

    models = q("SELECT model, COUNT(*) c FROM events"
               " WHERE kind = 'turn' AND ts >= datetime('now', ?)"
               " AND model != '' GROUP BY model")
    out["turns_by_model"] = {r["model"]: r["c"] for r in models}

    lat = q("SELECT AVG(latency_ms) a, MAX(latency_ms) m FROM events"
            " WHERE kind = 'turn' AND ts >= datetime('now', ?)")
    out["avg_latency_s"] = round((lat[0]["a"] or 0) / 1000, 1) if lat else 0
    out["max_latency_s"] = round((lat[0]["m"] or 0) / 1000, 1) if lat else 0

    jobs = q("SELECT status, COUNT(*) c FROM jobs"
             " WHERE created_at >= datetime('now', ?) GROUP BY status")
    out["jobs"] = {r["status"]: r["c"] for r in jobs}

    sched = q("SELECT COUNT(*) c FROM schedules WHERE active = 1", ())
    out["automations_active"] = sched[0]["c"] if sched else 0

    appr = q("SELECT status, COUNT(*) c FROM approvals"
             " WHERE created_at >= datetime('now', ?) GROUP BY status")
    out["approvals"] = {r["status"]: r["c"] for r in appr}

    evals = q("SELECT detail FROM events WHERE kind = 'eval'"
              " ORDER BY ts DESC LIMIT 1", ())
    out["last_eval"] = (evals[0]["detail"][:200]) if evals else ""

    last = q("SELECT MAX(ts) t FROM events WHERE kind = 'turn'", ())
    out["last_active"] = last[0]["t"] if last and last[0]["t"] else ""
    return out
