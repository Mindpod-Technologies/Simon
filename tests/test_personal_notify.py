"""Per-person notifications: work results reach the person who asked.

Covers the full chain: context attribution in tools → session columns →
Telegram routing per session → scheduler/job-runner delivery.
"""

import asyncio

import pytest

from simon import jobs, memory, schedules
from simon.config import Settings
from simon.context import current_session
from simon.interfaces import telegram_bot


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "simon.db"))
    return str(tmp_path / "simon.db")


SETTINGS = dict(
    telegram_bot_token="t",
    telegram_allowed_user_ids="111,222",
    simon_identity_map="slack:U1=owner,telegram:111=owner",
)


# --- session → telegram mapping --------------------------------------------

def test_raw_telegram_session_maps_to_itself():
    s = Settings(**SETTINGS)
    assert telegram_bot.session_to_telegram_id(s, "222") == 222


def test_owner_session_maps_through_identity_map():
    s = Settings(**SETTINGS)
    assert telegram_bot.session_to_telegram_id(s, "owner") == 111


def test_unknown_session_falls_back_to_owner():
    s = Settings(**SETTINGS)
    assert telegram_bot.session_to_telegram_id(s, "job-42") == 111
    assert telegram_bot.session_to_telegram_id(s, "") == 111


def test_notify_for_routes_per_session(monkeypatch):
    sent = []

    class FakeBot:
        def __init__(self, token=""):
            pass

        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    monkeypatch.setattr("telegram.Bot", FakeBot)
    s = Settings(**SETTINGS)
    notify_for = telegram_bot.make_notify_for(s)
    notify_for("222", "your schedule result")
    notify_for("owner", "owner notice")
    assert (222, "your schedule result") in sent
    assert (111, "owner notice") in sent


# --- attribution columns ----------------------------------------------------

def test_schedule_stores_creator_session(db):
    sid = schedules.add_schedule("water the family plants summary",
                                 hour=8, minute=15, session_id="222")
    row = next(r for r in schedules.list_schedules() if r["id"] == sid)
    assert row["session_id"] == "222"


def test_schedule_migration_adds_column(tmp_path):
    """An OLD database (no session_id column) is upgraded in place."""
    import sqlite3
    raw = str(tmp_path / "old.db")
    conn = sqlite3.connect(raw)
    conn.execute("CREATE TABLE schedules (id INTEGER PRIMARY KEY"
                 " AUTOINCREMENT, description TEXT NOT NULL,"
                 " hour INTEGER NOT NULL DEFAULT 9, minute INTEGER NOT NULL"
                 " DEFAULT 0, day_of_week TEXT DEFAULT '', active INTEGER"
                 " NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT"
                 " (datetime('now')))")
    conn.execute("INSERT INTO schedules (description) VALUES ('legacy task row')")
    conn.commit()
    conn.close()
    rows = schedules.list_schedules(path=raw)  # triggers init + migration
    assert rows[0]["session_id"] == ""


def test_reminder_stores_and_returns_session(db):
    memory.init_db()
    rid = memory.add_reminder("call grandma", "2000-01-01T00:00:00",
                              session_id="222")
    due = memory.due_reminders("2000-01-02T00:00:00")
    assert due[0]["id"] == rid
    assert due[0]["session_id"] == "222"


def test_tools_attribute_current_context(db):
    from simon.context import current_session
    from simon.tools.jobs_tool import _start_job
    from simon.tools.schedules_tool import _schedule_task

    token = current_session.set("222")
    try:
        out_j = _start_job("research family camping spots for spring break")
        out_s = _schedule_task("summarize school newsletter each week",
                               hour=18, minute=30, day_of_week="sun")
    finally:
        current_session.reset(token)
    assert "Job #" in out_j and "Schedule #" in out_s
    job = jobs.list_jobs(limit=1)[0]
    assert job["origin_session"] == "222"
    sched = schedules.list_schedules()[0]
    assert sched["session_id"] == "222"


# --- delivery paths ----------------------------------------------------------

def test_jobrunner_delivers_to_job_origin(db):
    delivered = []

    class FakeAgent:
        def handle(self, prompt):
            return "the deliverable"

    runner = jobs.JobRunner(
        agent_factory=lambda job_id: FakeAgent(),
        notify=lambda t: delivered.append(("owner", t)),
        notify_for=lambda s, t: delivered.append((s, t)),
        db_path=db)
    jobs.create_job("build the weekender list", origin_session="222", path=db)
    runner.run_once()
    assert delivered[0][0] == "222"  # pickup
    assert delivered[-1][0] == "222"  # completion
    assert "the deliverable" in delivered[-1][1]


def test_scheduler_task_delivers_to_creator(db):
    delivered = []

    class FakeAgent:
        def handle(self, prompt):
            return "weekly summary text"

    from simon.scheduler import Scheduler
    sched = Scheduler(Settings(), agent_factory=lambda: FakeAgent(),
                      notify=lambda t: delivered.append(("owner", t)),
                      notify_for=lambda s, t: delivered.append((s, t)))
    sid = schedules.add_schedule("prepare her Sunday school digest",
                                 hour=9, minute=0, session_id="222")
    asyncio.run(sched._run_scheduled_task(sid))
    assert delivered == [("222", delivered[0][1])]
    assert "weekly summary text" in delivered[0][1]


def test_scheduler_reminder_delivers_to_creator(db):
    delivered = []
    memory.init_db()
    memory.add_reminder("pack gym bag", "2000-01-01T00:00:00",
                        session_id="222")
    from simon.scheduler import Scheduler
    sched = Scheduler(Settings(), agent_factory=None,
                      notify=lambda t: delivered.append(("owner", t)),
                      notify_for=lambda s, t: delivered.append((s, t)))
    asyncio.run(sched._poll_reminders())
    assert delivered and delivered[0][0] == "222"
    assert "pack gym bag" in delivered[0][1]
