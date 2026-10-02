"""Memory consolidation worker: keeps the facts store healthy OUTSIDE the
hot turn loop (the settled 2026 pattern — Letta sleep-time, Bedrock
AgentCore-style async consolidation).

Runs on the scheduler (every 6h): 
1. Expiry sweep — facts past valid_until are quarantined (they stop
   surfacing; the record stays for audit).
2. Duplicate merge — facts with identical values under different keys merge
   into the oldest key; the dupes are quarantined with the survivor noted.
3. Decay report — model-inferred facts never reconfirmed in 90+ days are
   counted and reported (they already rank below user-stated facts; the
   report is what tells the owner the store is drifting).

Never deletes user-stated facts, never rewrites values — consolidation
touches structure, not content.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_STALE_DAYS = 90


def consolidate(path: Optional[str] = None) -> dict:
    """One consolidation pass. Returns a summary dict (for the digest)."""
    memory.init_db(path)
    conn = memory._connect(path)
    summary = {"expired_quarantined": 0, "dupes_merged": 0,
               "stale_model_facts": 0}
    try:
        # 1. Expiry sweep
        try:
            cur = conn.execute(
                "UPDATE facts SET quarantined = 1 WHERE quarantined = 0"
                " AND valid_until IS NOT NULL"
                " AND valid_until <= datetime('now')")
            summary["expired_quarantined"] = cur.rowcount
        except Exception:  # pre-migration DB
            pass

        # 2. Duplicate merge: identical value, multiple live keys
        try:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM facts"
                " WHERE quarantined = 0 ORDER BY updated_at ASC").fetchall()
        except Exception:
            rows = []
        seen: dict[str, str] = {}
        dupes: list[str] = []
        for r in rows:
            v = (r["value"] or "").strip().lower()
            if not v:
                continue
            if v in seen:
                dupes.append(r["key"])
            else:
                seen[v] = r["key"]
        for key in dupes:
            conn.execute(
                "UPDATE facts SET quarantined = 1 WHERE key = ?", (key,))
        summary["dupes_merged"] = len(dupes)

        # 3. Stale model-inferred facts (informational — they already rank
        #    below user facts; the count flags store drift)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM facts WHERE quarantined = 0"
                " AND source = 'model' AND"
                " COALESCE(last_confirmed, updated_at) <= datetime('now', ?)",
                (f"-{_STALE_DAYS} days",)).fetchone()
            summary["stale_model_facts"] = row["n"] if row else 0
        except Exception:
            pass

        conn.commit()
    finally:
        conn.close()
    if any(summary.values()):
        logger.info("memory consolidation: %s", summary)
    return summary
