"""Schedule tools: let the agent create recurring automations from chat."""

from __future__ import annotations

import logging

from .. import schedules
from .base import Tool

log = logging.getLogger(__name__)


def _schedule_task(description: str, hour: int = 9, minute: int = 0,
                   day_of_week: str = "") -> str:
    try:
        schedule_id = schedules.add_schedule(
            description, hour=hour, minute=minute, day_of_week=day_of_week)
    except ValueError as exc:
        return f"Error: {exc}"
    row = next(r for r in schedules.list_schedules() if r["id"] == schedule_id)
    log.info("schedule %d created: %s", schedule_id, description[:60])
    return (f"Schedule #{schedule_id} created: «{description[:80]}» — runs "
            f"{schedules.describe(row)}. It goes live within a minute and "
            f"results are delivered to the user each run. Confirm this to "
            f"the user in character.")


def _list_schedules() -> str:
    rows = schedules.list_schedules(active_only=True)
    if not rows:
        return "No recurring schedules on record."
    lines = ["Active recurring schedules:"]
    for r in rows:
        lines.append(f"#{r['id']} [{schedules.describe(r)}] "
                     f"{r['description'][:70]}")
    return "\n".join(lines)


def _cancel_schedule(schedule_id: int) -> str:
    if schedules.deactivate_schedule(int(schedule_id)):
        return (f"Schedule #{schedule_id} cancelled. It stops within a "
                f"minute.")
    return f"Error: no active schedule with id {schedule_id}."


def register_schedule_tools(registry, settings) -> None:
    """Register recurring-automation tools on ``registry``."""
    registry.register(Tool(
        name="schedule_task",
        description=(
            "Create a recurring automation: a task Simon runs on a schedule "
            "and delivers the result of each run. Use when the user asks for "
            "something to happen regularly ('every morning', 'each Monday', "
            "'weekly report'). One-off reminders belong in reminders, and "
            "big one-time tasks belong in start_job."),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": ("Full, self-contained task description — "
                                    "what to do and what to deliver each run.")},
                "hour": {"type": "integer",
                         "description": "Hour to run, 0-23 (local time)."},
                "minute": {"type": "integer",
                           "description": "Minute to run, 0-59."},
                "day_of_week": {
                    "type": "string",
                    "description": ("Empty = daily. Otherwise comma-separated "
                                    "days: mon,tue,wed,thu,fri,sat,sun.")},
            },
            "required": ["description", "hour", "minute"],
        },
        func=_schedule_task,
    ))
    registry.register(Tool(
        name="list_schedules",
        description="List Simon's active recurring automations/schedules.",
        parameters={"type": "object", "properties": {}},
        func=_list_schedules,
    ))
    registry.register(Tool(
        name="cancel_schedule",
        description="Cancel one of Simon's recurring automations by id.",
        parameters={
            "type": "object",
            "properties": {
                "schedule_id": {"type": "integer",
                                "description": "Schedule id to cancel."},
            },
            "required": ["schedule_id"],
        },
        func=_cancel_schedule,
    ))
