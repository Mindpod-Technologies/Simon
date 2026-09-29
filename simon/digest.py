"""Deterministic activity digest — the ONLY legal source for self-reports.

"What did you get done today?" is a question with a knowable, factual answer
(the event log). When the model answers it from vibes, it fabricates a work
day (observed live 2026-09-28: a full invented list of reviews and analyses
that never happened). This digest is computed from the obs event table and
the jobs table — ground truth, no model judgment involved.

Shared by the hourly status update (scheduler) and the status-question
grounding in the agent's prompt builder.
"""

from __future__ import annotations

import datetime
import json as _json
import logging

from . import memory, obs

logger = logging.getLogger(__name__)


def activity_digest(hours: float = 1.0) -> str:
    """Real recent activity from the event log — report ONLY from this."""
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=hours)).isoformat()
    try:
        events = [e for e in obs.recent_events(limit=300, kind="turn")
                  if e.get("ts", "") >= cutoff
                  and not (e.get("session_id") or "").startswith("eval")]
    except Exception:  # noqa: BLE001 - obs must never break a turn
        events = []
    lines = ["REAL activity log (from Simon's event table — "
             "report ONLY from this):"]
    if not events:
        lines.append("- No conversations and no tool calls in this window.")
    else:
        by_interface: dict[str, int] = {}
        tools: dict[str, int] = {}
        for e in events:
            iface = e.get("interface") or "?"
            by_interface[iface] = by_interface.get(iface, 0) + 1
            try:
                detail = _json.loads(e.get("detail") or "{}")
                inner = detail.get("detail", detail)
                if isinstance(inner, str):
                    inner = _json.loads(inner)
                for t in inner.get("tools", []):
                    tools[t] = tools.get(t, 0) + 1
            except Exception:  # noqa: BLE001
                pass
        lines.append("- Conversations: " + ", ".join(
            f"{k} ×{v}" for k, v in sorted(by_interface.items())))
        lines.append("- Tools used: " + (
            ", ".join(f"{k} ×{v}" for k, v in sorted(tools.items()))
            if tools else "none"))
    try:
        conn = memory._connect()
        try:
            rows = conn.execute(
                "SELECT id, status, description, finished_at FROM jobs "
                "WHERE created_at >= ? OR started_at >= ? OR "
                "finished_at >= ?",
                (cutoff[:19].replace("T", " "),
                 cutoff[:19].replace("T", " "),
                 cutoff[:19].replace("T", " "))).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        rows = []
    if rows:
        lines.append("- Background jobs: " + "; ".join(
            f"#{r[0]} {r[1]} — {r[2][:50]}" for r in rows[:5]))
    else:
        lines.append("- No background jobs ran.")
    return "\n".join(lines)
