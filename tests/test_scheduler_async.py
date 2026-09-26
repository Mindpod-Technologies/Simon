"""Scheduler agent turns must run off the event loop, and the mail-check
prompt must not invite hallucinated web fetches.

Regression: the background mail check called agent.handle() synchronously,
so one slow tool call (a 20 s fetch_url timeout) stalled the whole event
loop — scheduler jobs missed their windows and the Slack socket dropped
every 5 minutes.
"""

import asyncio
import time

import pytest

from simon.config import Settings
from simon.scheduler import Scheduler


class FakeAgent:
    def __init__(self, reply="MAIL_CHECK_QUIET", delay=0.0,
                 tools=("read_recent_emails",)):
        self.reply = reply
        self.delay = delay
        self.prompts = []
        # Receipts the real agent exposes after each turn; the mail-check
        # gate requires read_recent_emails among them before delivering.
        self.last_turn_tools = list(tools)

    def handle(self, prompt):
        self.prompts.append(prompt)
        if self.delay:
            time.sleep(self.delay)
        return self.reply


@pytest.fixture()
def idle(monkeypatch):
    """No interactive session active."""
    monkeypatch.setattr("simon.agent.interactive_session_active",
                        lambda: False)


def test_mail_check_prompt_forbids_web_fetches(idle):
    agent = FakeAgent()
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._mail_check())
    assert len(agent.prompts) == 1
    prompt = agent.prompts[0]
    assert "read_recent_emails" in prompt
    assert "ONLY" in prompt
    assert "not a website" in prompt          # mailbox ≠ website, spelled out
    assert sent == []                          # quiet reply → no interruption


def test_mail_check_reports_new_mail(idle):
    agent = FakeAgent(reply="New invoice from Acme, sir.")
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._mail_check())
    assert len(sent) == 1 and "Acme" in sent[0]


def test_mail_check_suppresses_fabricated_briefing(idle):
    """M3 receipts gate: a mail briefing with NO read_recent_emails receipt
    is fabricated mail — suppressed, never delivered."""
    agent = FakeAgent(reply="Urgent message from your bank, sir.",
                      tools=())  # claims mail, never called the tool
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._mail_check())
    assert sent == []


class _TaskFabricator:
    """Scheduled-task agent that claims completed work with no receipts;
    second call (the gate retry) keeps fabricating."""

    def __init__(self):
        self.last_turn_tools: list[str] = []
        self.calls = 0

    @staticmethod
    def _looks_dishonest(reply: str) -> bool:
        return "I have completed" in reply

    def handle(self, prompt: str) -> str:
        self.calls += 1
        self.last_turn_tools = []
        return "I have completed the pull and posted the summary."


def test_scheduled_task_gate_blocks_fabricated_result(idle, tmp_path,
                                                      monkeypatch):
    """M3: a scheduled task whose result claims actions with zero receipts
    must deliver an honest failure note, never the fabrication."""
    from simon import memory, schedules
    monkeypatch.setattr(memory, "DEFAULT_DB_PATH", str(tmp_path / "simon.db"))
    memory.init_db()
    schedule_id = schedules.add_schedule(
        "Pull last week's numbers and post a summary", hour=9)
    agent = _TaskFabricator()
    sent = []
    sched = Scheduler(Settings(), agent_factory=lambda: agent,
                      notify=sent.append)
    asyncio.run(sched._run_scheduled_task(schedule_id))
    assert agent.calls == 2                      # fabrication + gate retry
    assert len(sent) == 1
    assert "I have completed" not in sent[0]     # fabrication discarded
    assert "never actually produced" in sent[0]  # honest note delivered


def test_mail_check_does_not_block_the_event_loop(idle):
    """A slow agent turn must leave the loop responsive."""
    agent = FakeAgent(delay=0.4)
    sched = Scheduler(Settings(), agent_factory=lambda: agent, notify=None)
    ticks = []

    async def main():
        async def ticker():
            for _ in range(100):
                ticks.append(1)
                await asyncio.sleep(0.05)
        await asyncio.gather(sched._mail_check(), ticker())

    asyncio.run(main())
    # If handle() ran on the loop, the 0.4 s sleep would starve the ticker
    # to ~1 tick; off-loop it should tick roughly 0.4/0.05 ≈ 8 times.
    assert len(ticks) >= 4


def test_mail_check_defers_during_interactive_session(monkeypatch):
    monkeypatch.setattr("simon.agent.interactive_session_active",
                        lambda: True)
    agent = FakeAgent()
    sched = Scheduler(Settings(), agent_factory=lambda: agent, notify=None)
    asyncio.run(sched._mail_check())
    assert agent.prompts == []                # skipped entirely
