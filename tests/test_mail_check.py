"""Inbox watch: the scheduler's _mail_check must interrupt only when the
agent reports something new, and stay silent on the quiet sentinel."""

import asyncio

import pytest

from simon.scheduler import Scheduler
from simon.config import Settings
from simon import agent as agent_mod


@pytest.fixture(autouse=True)
def _reset_interactive_tracker():
    """Mail checks defer to live user turns — reset the shared tracker so
    tests stay independent of execution order."""
    agent_mod.LAST_INTERACTIVE.update(started=0.0, finished=0.0)
    yield
    agent_mod.LAST_INTERACTIVE.update(started=0.0, finished=0.0)


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
    sched = Scheduler(Settings(),
                      agent_factory=lambda: agent,
                      notify=sent.append)
    return sched, agent, sent


def test_mail_check_notifies_on_new_mail():
    sched, agent, sent = _make("1 new: CFO <cfo@x.com> — Budget sign-off needed")
    asyncio.run(sched._mail_check())
    assert len(sent) == 1
    assert "Budget sign-off" in sent[0]
    assert "read_recent_emails" in agent.prompts[0]


def test_mail_check_silent_on_quiet_sentinel():
    sched, _, sent = _make("MAIL_CHECK_QUIET")
    asyncio.run(sched._mail_check())
    assert sent == []


def test_mail_check_silent_on_empty_reply():
    sched, _, sent = _make("")
    asyncio.run(sched._mail_check())
    assert sent == []


def test_mail_check_swallows_agent_errors():
    def boom():
        raise RuntimeError("graph down")
    sent = []
    sched = Scheduler(Settings(), agent_factory=boom, notify=sent.append)
    asyncio.run(sched._mail_check())  # must not raise
    assert sent == []


def test_mail_check_defers_to_interactive_session():
    """On the single-GPU box a mail run evicts the chat model — it must
    skip its tick while the owner is mid-conversation."""
    import time
    agent_mod.LAST_INTERACTIVE.update(started=time.time(),
                                      finished=time.time())
    sched, agent, sent = _make("1 new: someone <x@y.com> — hi")
    asyncio.run(sched._mail_check())
    assert sent == []
    assert agent.prompts == []  # agent never even ran


def test_mail_check_noop_without_agent_factory():
    sent = []
    sched = Scheduler(Settings(), agent_factory=None, notify=sent.append)
    asyncio.run(sched._mail_check())
    assert sent == []


def _start_and_check(settings, job_id):
    async def run():
        from simon import memory
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
