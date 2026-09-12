"""User-defined recurring schedules: created in chat, stored in SQLite.

Simon creates these via the ``schedule_task`` tool ("every Monday at 9am,
pull last week's numbers and post a summary"). The Scheduler syncs them into
APScheduler on a short poll, so schedules created mid-conversation go live
without a restart and survive reboots.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import memory

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    hour INTEGER NOT NULL DEFAULT 9,
    minute INTEGER NOT NULL DEFAULT 0,
    day_of_week TEXT DEFAULT '',     -- '' = daily; else e.g. 'mon' or 'mon,wed,fri'
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def init_db(path: Optional[str] = None) -> None:
    conn = memory._connect(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def validate(day_of_week: str, hour: int, minute: int) -> Optional[str]:
    """Return an error string, or None if the schedule fields are valid."""
    if not (0 <= int(hour) <= 23):
        return f"hour must be 0-23, got {hour}"
    if not (0 <= int(minute) <= 59):
        return f"minute must be 0-59, got {minute}"
    if day_of_week:
        days = [d.strip().lower() for d in day_of_week.split(",")]
        bad = [d for d in days if d not in VALID_DAYS]
        if bad:
            return (f"invalid day(s) {bad} — use mon,tue,wed,thu,fri,sat,sun "
                    f"(comma-separated), or empty for daily")
    return None


def describe(row: dict) -> str:
    """Human-readable schedule, e.g. 'mon,wed at 09:30' or 'daily at 07:15'."""
    when = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    days = (row.get("day_of_week") or "").strip()
    return f"{days} at {when}" if days else f"daily at {when}"


def add_schedule(description: str, hour: int = 9, minute: int = 0,
                 day_of_week: str = "", path: Optional[str] = None) -> int:
    """Create an active schedule and return its id. Raises ValueError."""
    err = validate(day_of_week, hour, minute)
    if err:
        raise ValueError(err)
    description = (description or "").strip()
    if len(description) < 10:
        raise ValueError("task description too short — say what Simon "
                         "should actually do each run")
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "INSERT INTO schedules (description, hour, minute, day_of_week)"
            " VALUES (?, ?, ?, ?)",
            (description, int(hour), int(minute),
             (day_of_week or "").strip().lower()))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_schedules(active_only: bool = False,
                   path: Optional[str] = None) -> list[dict]:
    init_db(path)
    conn = memory._connect(path)
    try:
        sql = "SELECT * FROM schedules"
        if active_only:
            sql += " WHERE active = 1"
        rows = conn.execute(sql + " ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def deactivate_schedule(schedule_id: int, path: Optional[str] = None) -> bool:
    """Deactivate a schedule (kept in the DB for audit). False if unknown."""
    init_db(path)
    conn = memory._connect(path)
    try:
        cur = conn.execute(
            "UPDATE schedules SET active = 0 WHERE id = ? AND active = 1",
            (int(schedule_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
