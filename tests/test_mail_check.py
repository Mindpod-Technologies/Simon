"""Inbox watch: deterministic-first — read directly, dedupe by message id
(mailwatch), model only words the briefing, raw floor if it dodges.

Regression basis (2026-09-26 sweep): history-based dedupe lost to trimming
re-briefed the same weekly digest 19×, and question-shaped replies leaked
to the owner as alerts.
"""

import asyncio

import pytest

from simon.scheduler import Scheduler
from simon.config import Settings
from simon import agent as agent_mod
from simon import mailwatch, memory

INBOX = """1. Weekly digest [UNREAD]
   from: Microsoft 365 <msft@x.com>
   date: 2026-09-29T07:00:00Z
   Service updates for the week.
   id: msg-aaa

2. Invoice from Acme [UNREAD]
   from: Acme Billing <billing@acme.com>
   date: 2026-09-29T07:05:00Z
   Invoice #42 attached.
   id: msg-bbb"""


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    agent_mod.LAST_INTERACTIVE.update(started=0.0, finished=0.0)
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    memory.init_db()
    mailwatch.init_db()
    yield
    agent_mod.LAST_INTERACTIVE.update(started=0.0, finished=0.0)


@pytest.fixture()
def fake_graph(monkeypatch):
    monkeypatch.setattr("simon.tools.graph_mail.read_recent_emails_graph",
                        lambda settings, count=10: INBOX)


class FakeAgent:
    def __init__(self, reply):
        self._reply = reply
        self.prompts = []

    def handle(self, prompt):
        self.prompts.append(prompt)
        return self._reply


def _make(reply):
    agent = FakeAgent(reply)
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    return sched, agent, sent


def test_new_mail_briefed_once_then_never_again(fake_graph):
    sched, agent, sent = _make("Invoice #42 from Acme needs payment, sir.")
    asyncio.run(sched._mail_check())
    assert len(sent) == 1 and "Acme" in sent[0]
    # Second poll: same inbox, nothing new → NO model call, no delivery.
    asyncio.run(sched._mail_check())
    assert len(sent) == 1
    assert len(agent.prompts) == 1


def test_quiet_sentinel_marks_reported(fake_graph):
    """The model judged the mail uninteresting — reported, never repeated,
    and never re-litigated with another model call."""
    sched, agent, sent = _make("MAIL_CHECK_QUIET")
    asyncio.run(sched._mail_check())
    assert sent == []
    assert mailwatch.unseen(["msg-aaa", "msg-bbb"]) == []
    asyncio.run(sched._mail_check())
    assert len(agent.prompts) == 1  # no second turn


def test_dodge_reply_delivers_raw_floor(fake_graph):
    """A question-shaped 'briefing' is replaced by the deterministic floor —
    real senders and subjects, no prose."""
    sched, agent, sent = _make(
        "I see two unread emails. How can I help you with these?")
    asyncio.run(sched._mail_check())
    assert len(sent) == 1
    assert "Acme" in sent[0] and "msg-aaa" in sent[0] or "Weekly digest" in sent[0]
    assert "How can I help" not in sent[0]


def test_empty_inbox_no_model_call(monkeypatch):
    monkeypatch.setattr("simon.tools.graph_mail.read_recent_emails_graph",
                        lambda s, count=10: "Inbox is empty.")
    sched, agent, sent = _make("should never be consulted")
    asyncio.run(sched._mail_check())
    assert sent == [] and agent.prompts == []


def test_graph_error_no_model_call(monkeypatch):
    monkeypatch.setattr("simon.tools.graph_mail.read_recent_emails_graph",
                        lambda s, count=10: "graph mail: inbox read failed (401)")
    sched, agent, sent = _make("never")
    asyncio.run(sched._mail_check())
    assert sent == [] and agent.prompts == []


def test_mail_check_defers_to_interactive_session(monkeypatch, fake_graph):
    import time
    agent_mod.LAST_INTERACTIVE.update(started=time.time(),
                                      finished=time.time())
    sched, agent, sent = _make("never")
    asyncio.run(sched._mail_check())
    assert sent == [] and agent.prompts == []


def test_mail_check_noop_without_agent_factory(fake_graph):
    sent = []
    sched = Scheduler(Settings(), agent_factory=None, notify=sent.append)
    asyncio.run(sched._mail_check())
    assert sent == []


def _start_and_check(settings, job_id):
    async def run():
        memory.init_db()
        sched = Scheduler(settings)
        sched.start()
        try:
            return sched._scheduler.get_job(job_id) is not None
        finally:
            sched.shutdown()
    return asyncio.run(run())


def test_mail_check_registered_only_with_graph_creds(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    assert _start_and_check(
        Settings(graph_client_secret="s", simon_mail_check_minutes=5),
        "mail_check") is True
    assert _start_and_check(
        Settings(graph_client_secret=""), "mail_check") is False


def test_mail_check_respects_disable_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("simon.memory.DEFAULT_DB_PATH",
                        str(tmp_path / "simon.db"))
    assert _start_and_check(
        Settings(graph_client_secret="s", simon_mail_check_enabled=False),
        "mail_check") is False
