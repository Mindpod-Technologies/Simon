"""Onboarding moment: first contact → settled → tailored proposals."""

import sqlite3

import pytest

from simon import memory, onboarding
from simon.scheduler import Scheduler
from simon.config import Settings


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    return str(tmp_path / "simon.db")


def test_mark_is_idempotent(db):
    assert onboarding.mark("sess-1", "telegram") is True
    assert onboarding.mark("sess-1", "telegram") is False
    assert onboarding.mark("sess-2", "slack") is True


def test_due_only_settled_and_unsent(db):
    onboarding.mark("new-session", "telegram")
    # Freshly marked → NOT due (needs ONBOARDING_DELAY_MINUTES to pass).
    assert onboarding.due_for_proposals() == []
    conn = memory._connect()
    conn.execute(
        "UPDATE onboarded_sessions SET first_seen = datetime('now', '-3 hours')"
        " WHERE session_id = 'new-session'")
    conn.commit()
    conn.close()
    due = onboarding.due_for_proposals()
    assert len(due) == 1 and due[0]["session_id"] == "new-session"
    onboarding.mark_proposed("new-session")
    assert onboarding.due_for_proposals() == []


def test_build_context_uses_history_and_facts(db, tmp_path, monkeypatch):
    memory.init_db()
    memory.add_message("sess-x", "user", "I run a homeschool platform")
    memory.set_fact("wife's name", "Alicia")
    ctx = onboarding.build_context("sess-x")
    assert "homeschool platform" in ctx
    assert "Alicia" in ctx


def test_empty_context_is_honest(db):
    assert "just arrived" in onboarding.build_context("nobody-here")


def test_scheduler_delivers_and_marks(db):
    delivered = []
    memory.init_db()
    onboarding.mark("settled-session", "telegram")
    conn = memory._connect()
    conn.execute(
        "UPDATE onboarded_sessions SET first_seen = datetime('now', '-3 hours')")
    conn.commit()
    conn.close()
    memory.add_message("settled-session", "user", "I manage ad campaigns")

    class FakeAgent:
        def handle(self, prompt):
            assert "ad campaigns" in prompt
            return "1) Weekly ads digest 2) Spend anomaly watch 3) Report deck"

    import asyncio
    sched = Scheduler(Settings(), agent_factory=lambda: FakeAgent(),
                      notify=lambda t: delivered.append(("owner", t)),
                      notify_for=lambda s, t: delivered.append((s, t)))
    asyncio.run(sched._onboarding_proposals())
    assert delivered == [("settled-session",
                          delivered[0][1])]
    assert "Weekly ads digest" in delivered[0][1]
    assert onboarding.due_for_proposals() == []


def test_prompt_bans_generic_proposals():
    assert "banned" in onboarding.PROMPT
    assert "150 words" in onboarding.PROMPT
