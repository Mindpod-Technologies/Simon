"""Tests for user-defined recurring schedules (simon/schedules.py)."""

from __future__ import annotations

import pytest

from simon import schedules


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "test.db")


def test_add_and_list(db):
    sid = schedules.add_schedule("Pull last week's numbers", hour=9,
                                 minute=15, day_of_week="mon", path=db)
    rows = schedules.list_schedules(active_only=True, path=db)
    assert [r["id"] for r in rows] == [sid]
    assert schedules.describe(rows[0]) == "mon at 09:15"


def test_daily_default(db):
    sid = schedules.add_schedule("Check the markets each day", hour=8,
                                 minute=5, path=db)
    row = next(r for r in schedules.list_schedules(path=db) if r["id"] == sid)
    assert schedules.describe(row) == "daily at 08:05"


def test_validation(db):
    with pytest.raises(ValueError, match="hour"):
        schedules.add_schedule("a valid task description", hour=25, path=db)
    with pytest.raises(ValueError, match="minute"):
        schedules.add_schedule("a valid task description", minute=61, path=db)
    with pytest.raises(ValueError, match="invalid day"):
        schedules.add_schedule("a valid task description",
                               day_of_week="funday", path=db)
    with pytest.raises(ValueError, match="too short"):
        schedules.add_schedule("short", path=db)


def test_multi_day(db):
    sid = schedules.add_schedule("Gym reminder text here", hour=6, minute=45,
                                 day_of_week="Mon, Wed, Fri", path=db)
    row = next(r for r in schedules.list_schedules(path=db) if r["id"] == sid)
    assert row["day_of_week"] == "mon, wed, fri"


def test_deactivate(db):
    sid = schedules.add_schedule("Weekly sync prep task", path=db)
    assert schedules.deactivate_schedule(sid, path=db) is True
    assert schedules.deactivate_schedule(sid, path=db) is False
    assert schedules.list_schedules(active_only=True, path=db) == []
    # Kept for audit:
    assert len(schedules.list_schedules(path=db)) == 1


def test_schedule_tools(monkeypatch, db):
    # Exercise the tool functions against fakes for the store layer.
    from simon.tools import schedules_tool
    monkeypatch.setattr(schedules_tool.schedules, "add_schedule",
                        lambda desc, **kw: 7)
    monkeypatch.setattr(
        schedules_tool.schedules, "list_schedules",
        lambda **kw: [{"id": 7, "description": "weekly metrics pull",
                       "hour": 9, "minute": 30, "day_of_week": "fri",
                       "active": 1}])
    out = schedules_tool._schedule_task("weekly metrics pull", hour=9,
                                        minute=30, day_of_week="fri")
    assert "Schedule #7" in out and "fri at 09:30" in out
    listing = schedules_tool._list_schedules()
    assert "#7" in listing and "weekly metrics pull" in listing


def test_schedule_tool_rejects_invalid(monkeypatch):
    from simon.tools import schedules_tool
    def boom(*a, **kw):
        raise ValueError("hour must be 0-23, got 25")
    monkeypatch.setattr(schedules_tool.schedules, "add_schedule", boom)
    assert "Error" in schedules_tool._schedule_task("some task text", hour=25)


def test_schedule_tools_register():
    from simon.tools import ToolRegistry
    from simon.tools.schedules_tool import register_schedule_tools
    registry = ToolRegistry()
    register_schedule_tools(registry, settings=None)
    assert "schedule_task" in registry
    assert "list_schedules" in registry
    assert "cancel_schedule" in registry
